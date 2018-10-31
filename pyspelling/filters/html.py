"""
HTML filter.

Detect encoding from HTML header.
"""
from __future__ import unicode_literals
from .. import filters
import re
import codecs
import bs4
import html
from collections import namedtuple

NON_CONTENT = (bs4.Doctype, bs4.Declaration, bs4.CData, bs4.ProcessingInstruction)

RE_XML_START = re.compile(
    b'^(?:(' +
    b'<\\?xml[^>]+?>' +  # ASCII like
    b')|(' +
    re.escape('<?xml'.encode('utf-32-be')) + b'.+?' + re.escape('>'.encode('utf-32-be')) +
    b')|(' +
    re.escape('<?xml'.encode('utf-32-le')) + b'.+?' + re.escape('>'.encode('utf-32-le')) +
    b')|(' +
    re.escape('<?xml'.encode('utf-16-be')) + b'.+?' + re.escape('>'.encode('utf-16-be')) +
    b')|(' +
    re.escape('<?xml'.encode('utf-16-le')) + b'.+?' + re.escape('>'.encode('utf-16-le')) +
    b'))'
)

RE_HTML_ENCODE = re.compile(
    br'''(?xi)
    <\s*meta(?!\s*(?:name|value)\s*=)(?:[^>]*?content\s*=[\s"']*)?(?:[^>]*?)[\s"';]*charset\s*=[\s"']*([^\s"'/>]*)
    '''
)

RE_XML_ENCODE = re.compile(br'''(?i)^<\?xml[^>]*encoding=(['"])(.*?)\1[^>]*\?>''')
RE_XML_ENCODE_U = re.compile(r'''(?i)^<\?xml[^>]*encoding=(['"])(.*?)\1[^>]*\?>''')

RE_SELECTOR = re.compile(
    r'''(?x)
    ((?:\#|\.)[-\w]+) |                                           # .class and #id
    ((?:(?:[-\w\d]+|\*)\|)?(?:[-\w\d:]+|\*)|\|\*) |               # namespace:tag
    \[([\w\-:]+)(?:([~^|*$]?=)(\"[^"]+\"|'[^']'|[^'"\[\]]+))?\] | # attributes
    .+
    '''
)

MODE = {'html': 'lxml', 'xhtml': 'xml', 'html5lib': 'html5lib'}


class Selector(namedtuple('IgnoreRule', ['tag', 'namespace', 'id', 'classes', 'attributes'])):
    """Ignore rule."""


class SelectorAttribute(namedtuple('AttrRule', ['attribute', 'pattern'])):
    """Selector attribute rule."""


class HtmlFilter(filters.Filter):
    """Spelling Python."""

    block_tags = [
        # Block level elements (and other blockish elements)
        'address', 'article', 'aside', 'blockquote', 'details', 'dialog', 'dd',
        'div', 'dl', 'dt'
        'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3',
        'h4', 'h5', 'h6', 'header', 'hgroup', 'hr', 'li', 'main', 'menu', 'nav', 'ol', 'p', 'pre',
        'section', 'table', 'ul',
        'canvas', 'group', 'iframe', 'math', 'noscript', 'output',
        'script', 'style', 'table', 'video', 'body', 'head'
    ]

    def __init__(self, options, default_encoding='utf-8'):
        """Initialization."""

        self.comments = options.get('comments', True) is True
        self.attributes = set(options.get('attributes', []))
        self.mode = MODE.get(options.get('mode', 'html'), 'lxml')
        self.prefix = 'html' if self.mode != 'xml' else 'xhtml'
        self.selectors = self.process_selectors(*options.get('ignores', []))
        super(HtmlFilter, self).__init__(options, default_encoding)

    def _has_xml_encode(self, content):
        """Check XML encoding."""

        encode = None

        m = RE_XML_START.match(content)
        if m:
            if m.group(1):
                m2 = RE_XML_ENCODE.match(m.group(1))

                if m2:
                    enc = m2.group(2).decode('ascii')

                    try:
                        codecs.getencoder(enc)
                        encode = enc
                    except LookupError:
                        pass
            else:
                if m.group(2):
                    enc = 'utf-32-be'
                    text = m.group(2)
                elif m.group(3):
                    enc = 'utf-32-le'
                    text = m.group(3)
                elif m.group(4):
                    enc = 'utf-16-be'
                    text = m.group(4)
                elif m.group(5):
                    enc = 'utf-16-le'
                    text = m.group(5)
                try:
                    m2 = RE_XML_ENCODE_U.match(text.decode(enc))
                except Exception:  # pragma: no cover
                    m2 = None

                if m2:
                    enc = m2.group(2)

                    try:
                        codecs.getencoder(enc)
                        encode = enc
                    except Exception:
                        pass

        return encode

    def header_check(self, content):
        """Special HTML encoding check."""

        encode = None

        # Look for meta charset
        m = RE_HTML_ENCODE.search(content)
        if m:
            enc = m.group(1).decode('ascii')

            try:
                codecs.getencoder(enc)
                encode = enc
            except LookupError:
                pass
        else:
            encode = self._has_xml_encode(content)
        return encode

    def is_block(self, el):
        """Check if tag is a block element."""

        return el.name.lower() in self.block_tags

    def process_selectors(self, *args):
        """
        Process selectors.

        We do our own selectors as BeautifulSoup4 has some annoying quirks,
        and we don't really need to do nth selectors or siblings or
        descendants etc.
        """

        selectors = [
            Selector('style', None, None, tuple(), tuple()),
            Selector('script', None, None, tuple(), tuple()),
        ]

        for selector in args:
            tag = None
            tag_id = None
            namespace = None
            classes = set()
            attributes = []
            is_xml = self.mode != 'xml'

            for m in RE_SELECTOR.finditer(selector):
                if m.group(3):
                    attr = m.group(3).lower() if not is_xml else m.group(3)
                    op = m.group(4)
                    if op:
                        value = m.group(5)[1:-1] if m.group(5).startswith('"') else m.group(5)
                    else:
                        value = None
                    if not op:
                        # Attribute name
                        pattern = None
                    elif op.startswith('^'):
                        # Value start with
                        pattern = re.compile(r'^%s.*' % re.escape(value))
                    elif op.startswith('$'):
                        # Value ends with
                        pattern = re.compile(r'.*?%s$' % re.escape(value))
                    elif op.startswith('*'):
                        # Value contains
                        pattern = re.compile(r'.*?%s.*' % re.escape(value))
                    elif op.startswith('~'):
                        # Value contains word within space separated list
                        pattern = re.compile(r'.*?(?:(?<=^)|(?<= ))%s(?=(?:[ ]|$)).*' % re.escape(value))
                    elif op.startswith('|'):
                        # Value starts with word in dash separated list
                        pattern = re.compile(r'^%s(?=-).*' % re.escape(value))
                    else:
                        # Value matches
                        pattern = re.compile(r'^%s$' % re.escape(value))
                    attributes.append(SelectorAttribute(attr, pattern))
                elif m.group(1):
                    selector = m.group(0).lower()
                    if selector.startswith('.'):
                        classes.add(selector[1:].lower())
                    elif selector.startswith('#') and tag_id is None:
                        tag_id = selector[1:]
                elif m.group(2):
                    selector = m.group(0)
                    parts = selector.split('|')
                    if tag is None:
                        if len(parts) > 1:
                            namespace = parts[0]
                            tag = parts[1]
                        else:
                            tag = parts[0]
                    else:
                        raise ValueError('Bad selector!')
                else:
                    raise ValueError('Bad selector!')

            if tag or tag_id or classes:
                selectors.append(Selector(tag, namespace, tag_id, tuple(classes), tuple(attributes)))

        return selectors

    def get_classes(self, el):
        """Get classes."""

        if self.mode != 'xml':
            return el.attrs.get('class', [])
        else:
            return [c for c in el.attrs.get('class', '').strip().split(' ') if c]

    def skip_tag(self, el):
        """Determine if tag should be skipped."""

        skip = False
        for selector in self.selectors:
            if (
                selector.namespace is not None and
                selector.namespace is not '*' and
                (
                    (el.namespace is None and selector.namespace) or
                    (el.namespace is not None and el.namespace != selector.namespace)
                )
            ):
                print('really?')
                continue
            if selector.tag and selector.tag not in ((el.name.lower() if self.mode != 'xml' else el.name), '*'):
                print(el.name, selector.tag)
                print('really 2?')
                continue
            if selector.id and selector.id != el.attrs.get('id', '').lower():
                print('really 3?')
                continue
            if selector.classes:
                current_classes = [c.lower() for c in self.get_classes(el)]
                found = True
                for c in selector.classes:
                    if c not in current_classes:
                        found = False
                        break
                if not found:
                    print('really 4?')
                    continue
            if selector.attributes:
                found = True
                for a in selector.attributes:
                    value = el.attrs.get(a.attribute)
                    if isinstance(value, list):
                        value = ' '.join(value)
                    if not value:
                        found = False
                        break
                    elif a.pattern is None:
                        continue
                    elif a.pattern.match(value) is None:
                        found = False
                        break
                if not found:
                    print('really 5?')
                    continue
            skip = True
            break
        return skip

    def construct_selector(self, el, attr=''):
        """Construct an selector for context."""

        tag = el.name
        prefix = el.prefix
        classes = self.get_classes(el)
        tag_id = el.attrs.get('id', '').strip()
        sel = ''
        if prefix:
            sel += prefix + '|'
        sel += tag
        if tag_id:
            sel += '#' + tag_id
        if classes:
            sel += '.' + '.'.join(classes)
        if attr:
            sel += '[%s]' % attr
        return sel

    def html_to_text(self, tree):
        """
        Parse the HTML creating a buffer with each tags content.

        Skip any selectors specified and include attributes if specified.
        Ignored tags will not have their attributes scanned either.
        """

        text = []
        attributes = []
        comments = []
        blocks = []
        root = tree.name == '[document]'

        # Handle comments
        # TODO: This might not be needed as comment objects may never get fed in this way.
        if isinstance(tree, bs4.Comment):  # pragma: no cover
            if self.comments:
                string = str(tree).strip()
                if string:
                    sel = self.construct_selector(tree) + '<!--comment-->'
                    comments.append((html.unescape(string), sel))
        elif root or not self.skip_tag(tree):
            # Check attributes for normal tags
            if not root:
                for attr in self.attributes:
                    value = tree.attrs.get(attr, '').strip()
                    if value:
                        sel = self.construct_selector(tree, attr=attr)
                        attributes.append((html.unescape(value), sel))

            # Walk children
            for child in tree:
                is_comment = isinstance(child, bs4.Comment)
                if isinstance(child, bs4.element.Tag):
                    t, b, a, c = (self.html_to_text(child))
                    text.extend(t)
                    attributes.extend(a)
                    comments.extend(c)
                    blocks.extend(b)
                # Get content if not the root and not a comment (unless we want comments).
                elif not isinstance(child, NON_CONTENT) and (not is_comment or self.comments):
                    string = str(child).strip()
                    if string:
                        if is_comment:
                            sel = self.construct_selector(tree) + '<!--comment-->'
                            comments.append((html.unescape(string), sel))
                        else:
                            text.append(string)
                            text.append(' ')

        if root or self.is_block(tree):
            content = html.unescape(''.join(text))
            if content:
                blocks.append((content, self.construct_selector(tree)))
            text = []

        if root:
            return blocks, attributes, comments
        else:
            return text, blocks, attributes, comments

    def _filter(self, text, context, encoding):
        """Filter the source text."""

        content = []
        blocks, attributes, comments = self.html_to_text(bs4.BeautifulSoup(text, self.mode))
        if self.comments:
            for c, desc in comments:
                content.append(filters.SourceText(c, context + ': ' + desc, encoding, self.prefix + 'comment'))
        if self.attributes:
            for a, desc in attributes:
                content.append(filters.SourceText(a, context + ': ' + desc, encoding, self.prefix + 'attribute'))
        for b, desc in blocks:
            content.append(filters.SourceText(b, context + ': ' + desc, encoding, self.prefix + 'content'))
        return content

    def filter(self, source_file, encoding):  # noqa A001
        """Parse HTML file."""

        with codecs.open(source_file, 'r', encoding=encoding) as f:
            text = f.read()
        return self._filter(text, source_file, encoding)

    def sfilter(self, source):
        """Filter."""

        return self._filter(source.text, source.context, source.encoding)


def get_plugin():
    """Return the filter."""

    return HtmlFilter

"""Safe, portable catalog content shared by the console, H5 and WeChat."""
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .private_media import public_media_path

MAX_CONTENT_LENGTH = 100000
TAGS = {'p', 'h2', 'h3', 'strong', 'b', 'em', 'i', 'u', 's', 'ul', 'ol',
        'li', 'blockquote', 'br', 'hr', 'img'}
VOID = {'br', 'hr', 'img'}
DROP = {'script', 'style', 'iframe', 'object', 'embed', 'svg', 'math', 'template'}


def image_source(value):
    value = str(value or '').strip()
    if not value or any(ord(c) < 32 for c in value) or '\\' in value:
        return ''
    try:
        parts = urlsplit(value)
    except ValueError:
        return ''
    if value.startswith('/media/'):
        return value if public_media_path(value) and not parts.query and not parts.fragment else ''
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password:
        return ''
    # A public URL must never make private business images public.
    if parts.path.startswith('/media/') and not public_media_path(value):
        return ''
    return value


class CatalogHTML(HTMLParser):
    def __init__(self, display_images=False):
        self.display_images = display_images
        super().__init__(convert_charrefs=True)
        self.output, self.stack, self.images, self.blocked = [], [], [], []

    def handle_starttag(self, tag, attrs):
        if self.blocked:
            if tag in DROP:
                self.blocked.append(tag)
            return
        if tag in DROP:
            self.blocked.append(tag)
            return
        if tag not in TAGS:
            return
        if tag == 'img':
            attrs = dict(attrs)
            src = image_source(attrs.get('src'))
            if not src:
                return
            if self.display_images:
                from .catalog_images import catalog_display_url
                src = catalog_display_url(src)
                if not src:
                    return
            alt = escape(str(attrs.get('alt') or '')[:200], quote=True)
            self.output.append(f'<img src="{escape(src, quote=True)}" alt="{alt}" style="max-width:100%;height:auto;display:block" />')
            if src not in self.images:
                self.images.append(src)
        elif tag in VOID:
            self.output.append(f'<{tag} />')
        else:
            self.output.append(f'<{tag}>')
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
            return
        if tag in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.output.append(f'</{current}>')
                if current == tag:
                    break

    def handle_data(self, data):
        if not self.blocked:
            self.output.append(escape(data))

    def result(self):
        return ''.join(self.output + [f'</{tag}>' for tag in reversed(self.stack)])


def sanitize_catalog_html(value):
    if len(value) > MAX_CONTENT_LENGTH:
        raise ValueError('玩法详情不能超过 100000 个字符')
    parser = CatalogHTML()
    parser.feed(value)
    parser.close()
    return parser.result()


def catalog_content(service):
    """An individual price tier overrides the shared gameplay document."""
    value = service.details_html or (service.gameplay.details_html if service.gameplay_id else '')
    parser = CatalogHTML(display_images=True)
    parser.feed(value)
    parser.close()
    return {'html': parser.result(), 'images': parser.images}

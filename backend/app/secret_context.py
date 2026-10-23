"""Sensitive data leaves, separate from typed browser/protocol metadata."""
import re

AUTH_FIELD_SELECTORS = {'#username', '#password', '#login', '.username', '.password', '.login'}
ARIA_ROLES = frozenset('''alert alertdialog application article banner blockquote button caption cell checkbox code columnheader
combobox complementary contentinfo definition deletion dialog directory document emphasis feed figure form generic grid gridcell
group heading img insertion link list listbox listitem log main marquee math menu menubar menuitem menuitemcheckbox menuitemradio
meter navigation none note option paragraph presentation progressbar radio radiogroup region row rowgroup rowheader scrollbar
search searchbox separator slider spinbutton status strong subscript superscript switch tab table tablist tabpanel term textbox
time timer toolbar tooltip tree treegrid treeitem'''.split())


def contains(value, secret):
    # A one-character account can occur incidentally in every English identifier.
    # Still detect it as a value, quoted JSON value, URL segment or text token.
    if len(secret) <= 2 and secret.isalnum():
        return re.search(r'(?<![A-Za-z0-9_])' + re.escape(secret) + r'(?![A-Za-z0-9_])', value) is not None
    return secret in value


def data_leaves(value):
    """Yield sensitive leaves; callers supply validated application structures."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from data_leaves(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key == 'default' and value.get('kind') == 'credential':
                # Validated credential parameters contain a local reference, not a value.
                continue
            if key == 'id' and isinstance(child,str) and re.fullmatch(r'(?:[0-9a-f]{32}|(?:login|action|condition)_\d+)',child):
                continue
            if key == 'role' and (child is None or child in ARIA_ROLES):
                continue
            if key in {'kind', 'input_type', 'tag', 'node_id', 'form_id',
                       'draft_id', 'credential_ref', 'credential_parameter', 'url_parameter',
                       'field', 'status'}:
                continue
            if key == 'value' and value.get('kind') == 'css':
                # Fixed authentication field names can equal protocol literals.
                # Arbitrary edited identities must still undergo data checks.
                if child in AUTH_FIELD_SELECTORS:
                    continue
            yield from data_leaves(child)

"""Translations of the Capybara Editor's own windows (Item Creator, Character window, inventory panel, import...).

T('key', 'English text {field}', field=...) looks the key up in the language file chosen in the editor
(lang/<code>.json, same files and same Language setting as the collector's texts) and falls back to the English
text written at the call. Missing keys therefore never break a window: they just stay in English.

Game text (item, stat, skill, quest and waypoint names, tooltip lines) comes from Median XL's own tables and stays
as the game shows it."""
try:
    import i18n
except Exception:                     # tests without the collector
    i18n = None


def T(key, default, **fields):
    txt = None
    if i18n is not None:
        try:
            txt = (getattr(i18n, '_texts', None) or {}).get(key)
        except Exception:
            txt = None
    if not txt:
        txt = default
    if fields:
        try:
            return txt.format(**fields)
        except (KeyError, IndexError, ValueError):
            try:
                return default.format(**fields)
            except (KeyError, IndexError, ValueError):
                return default
    return txt

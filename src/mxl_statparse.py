"""Text -> item stats: turns the stat lines of an item tooltip (as the game / median-xl.com show them) back into
(stat id, param, value) entries, using the collector's own tooltip renderer as the reference.

For every stat (and every display group such as "+5 to all Attributes") a pattern is made from the text the
renderer gives for a sample value. A line is matched against the patterns, the numbers it contains give candidate
values, and a candidate is only accepted when the renderer gives back exactly the same line for it."""
import re
from collections import defaultdict

from mxl_tooltip import stat_lines

SAMPLE = 4321
NUM = r'(-?\d+)'


def _norm(text):
    text = re.sub(r'ÿc.', '', str(text))
    return re.sub(r'\s+', ' ', text).strip()


def canon(text):
    """Comparison form: the site and the collector word a few stats differently ("+10%" / "10%",
    "1 second" / "1 seconds", untranslated "tooltip.random_monster")."""
    t = _norm(text).replace('tooltip.random_monster', 'Random Monster')
    t = re.sub(r'(?<![\w-])\+(?=\d)', '', t)
    t = re.sub(r'\bseconds\b', 'second', t)
    return t.lower()


class StatParser:
    def __init__(self, data, char_level=1):
        self.d = data
        self.char = {'level': char_level}
        self.skill_ids = defaultdict(list)            # display name -> [skill id]
        for sid, name in data.skill_names.items():
            if name:
                self.skill_ids[canon(name)].append(sid)
        self.rules = []                                # (regex, kind, ids)
        self._build()

    # ------------------------------------------------------------------ rendering
    def render(self, stats):
        return [t for t, c in self.render_cols(stats)]

    def render_cols(self, stats):
        try:
            return [(_norm(l[1]), str(l[0])) for l in stat_lines(stats, self.d, self.char) if _norm(l[1])]
        except Exception:
            return []

    def _same(self, stats, line):
        out = self.render(stats)
        return len(out) == 1 and canon(out[0]) == canon(line)

    def _one(self, stats):
        out = self.render(stats)
        return out[0] if len(out) == 1 else None

    @staticmethod
    def _pattern(text, numbers=(SAMPLE,)):
        """Regex of a rendered line: the sample numbers become number groups."""
        pat = re.escape(text)
        for n in numbers:
            pat = pat.replace(re.escape(str(n)), NUM, 1)
        return pat

    # ------------------------------------------------------------------ rules
    def _build(self):
        d = self.d
        isc = d.isc
        groups = defaultdict(list)
        for sid, s in isc.items():
            if isinstance(s, dict) and s.get('dgrp'):
                groups[s['dgrp']].append(sid)
        for ids in groups.values():                    # "+5 to all Attributes", "Elemental Resists +5%"...
            if len(ids) < 2:
                continue
            for v in (SAMPLE, -SAMPLE):
                t = self._one([dict(id=i, param=None, value=v) for i in ids])
                if t and str(abs(v)) in t:
                    self.rules.append((re.compile('^' + self._pattern(t, (abs(v),)) + '$'), 'group', tuple(ids)))
        # damage pairs: "Adds 13-38 Damage", "Adds 30-35 Cold Damage"
        for lo, hi, extra in ((21, 22, ()), (48, 49, ()), (50, 51, ()), (52, 53, ()), (54, 55, (56,)), (57, 58, (59,))):
            if lo not in isc or hi not in isc:
                continue
            st = [dict(id=lo, param=None, value=1234), dict(id=hi, param=None, value=5678)]
            st += [dict(id=e, param=None, value=50) for e in extra]
            for t in self.render(st):
                if '1234' in t and '5678' in t:
                    pat = self._pattern(t, (1234, 5678))
                    self.rules.append((re.compile('^' + pat + '$'), 'pair', (lo, hi) + tuple(extra)))
            # same min and max: the game shows one number ("+20 Damage", "+20 Fire Damage")
            st = [dict(id=lo, param=None, value=1234), dict(id=hi, param=None, value=1234)]
            st += [dict(id=e, param=None, value=50) for e in extra]
            for t in self.render(st):
                if t.count('1234') == 1:
                    pat = self._pattern(t, (1234,))
                    self.rules.append((re.compile('^' + pat + '$'), 'pair_eq', (lo, hi) + tuple(extra)))
        params = {83: range(0, 8), 188: range(0, 8 * 8)}
        for sid, s in isc.items():
            if isinstance(s, dict) and s.get('func') == 38 and s.get('param'):
                params[sid] = range(0, 1 << min(s['param'], 6))
            if isinstance(s, dict) and s.get('func') == 33:
                sk = self._any_skill()
                t = self._one([dict(id=sid, param=sk, value=25 * 3)])
                if t:
                    name = _norm(d.skill_names[sk])
                    pat = re.escape(t).replace(re.escape(name), '(?P<skill>.+?)', 1)
                    pat = re.sub(r'3(\\\.0)?', lambda m: r'(?P<num>[\d.]+)', pat, count=1)
                    self.rules.append((re.compile('^' + pat + '$'), 'cooldown', (sid,)))
            if isinstance(s, dict) and s.get('func') == 23:
                self.rules.append((re.compile(r'^(\d+)% (?:\S.*?): ?(?P<mon>.*)$'), 'reanimate', (sid,)))
        for sid, s in isc.items():
            if not isinstance(s, dict) or not s.get('bits'):
                continue
            func = s.get('func')
            if func in (27, 28):                       # "+3 to Teleport": single / non-class skill
                t = self._one([dict(id=sid, param=self._any_skill(), value=SAMPLE)])
                if t:
                    name = _norm(d.skill_names[self._any_skill()])
                    pat = self._pattern(t).replace(re.escape(name), '(?P<skill>.+?)')
                    self.rules.append((re.compile('^' + pat + '$'), 'skill', (sid,)))
                continue
            if s.get('param') and sid in params:
                for p in params[sid]:
                    t = self._one([dict(id=sid, param=p, value=SAMPLE)])
                    if t and str(SAMPLE) in t and '%d' not in t:
                        self.rules.append((re.compile('^' + self._pattern(t) + '$'), 'param', (sid, p)))
                continue
            if s.get('param'):
                sk = self._any_skill()
                samples = (((sk << 6) | 37, 23 | (29 << 8)),) if s.get('func') == 24 else (((sk << 6) | 37, 23),)
                for p, v in samples:
                    t = self._one([dict(id=sid, param=p, value=v)])
                    if t and '37' in t:
                        name = _norm(d.skill_names[sk])
                        pat = re.escape(t).replace(re.escape(name), '(?P<skill>.+?)')
                        pat = pat.replace('37', '(?P<lvl>\\d+)', 1)
                        if v > 255:
                            pat = pat.replace('23', '(?P<cur>\\d+)', 1).replace('29', '(?P<max>\\d+)', 1)
                        else:
                            pat = pat.replace('23', '(?P<val>\\d+)', 1)
                        self.rules.append((re.compile('^' + pat + '$'), 'cast', (sid,)))
                        break
                continue
            for v in (SAMPLE, -SAMPLE):
                t = self._one([dict(id=sid, param=None, value=v)])
                if not t:
                    continue
                if str(abs(v)) in t:
                    self.rules.append((re.compile('^' + self._pattern(t, (abs(v),)) + '$'), 'single', (sid,)))
                elif not re.search(r'\d', t):
                    self.rules.append((re.compile('^' + re.escape(t) + '$'), 'flag', (sid,)))
                    break
                else:                                   # value shown transformed (per level, /256...)
                    pat = re.sub(r'\d+', lambda m: NUM, re.escape(t))
                    self.rules.append((re.compile('^' + pat + '$'), 'single', (sid,)))
        # patterns work on the comparison form (lower case, no "+" before numbers)
        fixed = []
        for rx, kind, ids in self.rules:
            pat = rx.pattern
            pat = re.sub(r'(?<![\\\w-])\\\+(?=\(|\d)', '', pat)
            pat = pat.replace('seconds', 'second')
            fixed.append((re.compile(pat, re.I), kind, ids))
        self.rules = fixed
        # groups and pairs first, longer patterns before shorter ones
        order = {'pair': 0, 'pair_eq': 0, 'group': 1, 'cast': 2, 'cooldown': 2, 'param': 3, 'single': 4, 'skill': 5, 'reanimate': 6, 'flag': 7}
        self.rules.sort(key=lambda r: (order[r[1]], -len(r[0].pattern)))

    def _any_skill(self):
        if not hasattr(self, '_sk'):
            self._sk = next(k for k, v in self.d.skill_names.items() if v and len(_norm(v)) > 6 and 'unknown' not in v)
        return self._sk

    # ------------------------------------------------------------------ matching
    def _candidates(self, n):
        out = [n, -n]
        for k in (2, 4, 8, 16, 32, 64, 128, 256):
            out += [n * k, -n * k]
        for k in (10, 20, 25, 100):
            out += [n * k + e for e in range(k)]
        lvl = max(1, self.char['level'])
        for shift in (1, 2, 3, 4, 5, 6, 7, 8):
            base = n * (1 << shift) / lvl
            out += [int(base) + e for e in range(-2, 3)]
        seen, res = set(), []
        for v in out:
            if v not in seen:
                seen.add(v)
                res.append(v)
        return res

    def match(self, line):
        """Stats [dict(id, param, value)] of one tooltip line, or None."""
        line = _norm(line)
        m = re.fullmatch(r'\+?(-?\d+)% Enhanced Damage', line)
        if m:                                           # weapon ED: min and max damage % together
            v = int(m.group(1))
            return [dict(id=17, param=None, value=v), dict(id=18, param=None, value=v)]
        cl = canon(line)
        for rx, kind, ids in self.rules:
            m = rx.match(cl)
            if not m:
                continue
            if kind == 'flag':
                for v in (1, 2, 3, 4, 5):
                    st = [dict(id=ids[0], param=None, value=v)]
                    if self._same(st, line):
                        return st
                return [dict(id=ids[0], param=None, value=1)]
            if kind == 'group':
                n = int(m.group(1))
                for v in self._candidates(n):
                    st = [dict(id=i, param=None, value=v) for i in ids]
                    if self._same(st, line):
                        return st
                continue
            if kind == 'pair':
                a, b = int(m.group(1)), int(m.group(2))
                st = [dict(id=ids[0], param=None, value=a), dict(id=ids[1], param=None, value=b)]
                st += [dict(id=e, param=None, value=50) for e in ids[2:]]
                if self._same(st, line):
                    return st
                continue
            if kind == 'pair_eq':
                a = int(m.group(1))
                st = [dict(id=ids[0], param=None, value=a), dict(id=ids[1], param=None, value=a)]
                st += [dict(id=e, param=None, value=50) for e in ids[2:]]
                if self._same(st, line):
                    return st
                continue
            if kind == 'param':
                n = int(m.group(1))
                for v in self._candidates(n):
                    st = [dict(id=ids[0], param=ids[1], value=v)]
                    if self._same(st, line):
                        return st
                continue
            if kind == 'skill':
                n = int(re.search(NUM, line).group(1))
                name = m.group('skill')
                cand = list(self.skill_ids.get(canon(name), []))
                for suffix in (' (', ):
                    if not cand and suffix in name:
                        cand = list(self.skill_ids.get(canon(name.split(suffix)[0]), []))
                for sk in cand:
                    st = [dict(id=ids[0], param=sk, value=n)]
                    if self._same(st, line):
                        return st
                continue
            if kind == 'cooldown':
                name, num = m.group('skill'), float(m.group('num'))
                for sk in self.skill_ids.get(canon(name), []):
                    for v in range(max(0, int(num * 25) - 13), int(num * 25) + 14):
                        st = [dict(id=ids[0], param=sk, value=v)]
                        if self._same(st, line):
                            return st
                continue
            if kind == 'reanimate':
                n, mon = int(m.group(1)), m.group('mon').strip()
                names = getattr(self.d, 'monster_names', [])
                cands = [i for i, x in enumerate(names) if canon(x) == canon(mon)] or [len(names)]
                for p in cands:
                    st = [dict(id=ids[0], param=p, value=n)]
                    if self._same(st, line):
                        return st
                continue
            if kind == 'cast':
                name = m.group('skill')
                lvl = int(m.group('lvl'))
                for sk in self.skill_ids.get(canon(name), []):
                    p = (sk << 6) | lvl
                    if 'val' in m.groupdict():
                        v = int(m.group('val'))
                    else:
                        v = int(m.group('cur')) | (int(m.group('max')) << 8)
                    st = [dict(id=ids[0], param=p, value=v)]
                    if self._same(st, line):
                        return st
                continue
            # single
            nums = [int(x) for x in m.groups() if x is not None and re.fullmatch(r'-?\d+', x)]
            if not nums:
                continue
            for v in self._candidates(nums[0]):
                st = [dict(id=ids[0], param=None, value=v)]
                if self._same(st, line):
                    return st
        return None

    def _text_index(self):
        if not hasattr(self, '_tix'):
            self._tix = defaultdict(list)
            for i, ent in self.d.strings.items():
                if i >= 1 << 15:
                    continue
                for part in str(ent[1]).replace('\\n', '\n').split('\n'):
                    t = canon(part)
                    if t:
                        self._tix[t].append(i)
            self._text_sids = [sid for sid, c in self.d.isc.items()
                               if isinstance(c, dict) and c.get('func') in (31, 34) and c.get('bits')]
        return self._tix

    def parse(self, lines):
        """(stats, unmatched lines) of a list of lines (text or (text, colour)); equal stats are added up.
        Text stats (a string shown as is, e.g. "Goblin Glee lasts 3 extra seconds ...") take all their lines."""
        items = [(_norm(l[0]), str(l[1])) if isinstance(l, (tuple, list)) else (_norm(l), None) for l in lines]
        used = [not t for t, c in items]
        ruled = [self.match(t) if t else None for t, c in items]
        stats, missing = [], []
        # 1. text stats, found from a line no stat pattern explains; they take all their lines
        for k, (line, col) in enumerate(items):
            if used[k] or ruled[k] is not None:
                continue
            found = None
            for idx in self._text_index().get(canon(line), []):
                for sid in self._text_sids:
                    out = self.render_cols([dict(id=sid, param=idx, value=1)])
                    texts = [canon(t) for t, c in out]
                    if canon(line) not in texts:
                        continue
                    pos = []
                    for t in texts:
                        j = next((j for j in range(len(items)) if not used[j] and j not in pos and canon(items[j][0]) == t), None)
                        if j is None:
                            break
                        pos.append(j)
                    else:
                        cols_ok = col is None or any(c == col for t, c in out if canon(t) == canon(line))
                        if found is None or (cols_ok and not found[2]) or (cols_ok == found[2] and len(pos) > len(found[1])):
                            found = ([dict(id=sid, param=idx, value=1)], pos, cols_ok)
            if found:
                stats += found[0]
                for j in found[1]:
                    used[j] = True
        # 2. stat patterns
        for k, (line, col) in enumerate(items):
            if used[k]:
                continue
            if ruled[k] is not None:
                stats += ruled[k]
            else:
                missing.append(line)
        return merge(stats), missing


def merge(stats):
    out = {}
    for s in stats:
        k = (s['id'], s.get('param'))
        if k in out:
            out[k]['value'] += s['value']
        else:
            out[k] = dict(id=s['id'], param=s.get('param'), value=s['value'])
    return list(out.values())


def subtract(stats, minus):
    """stats - minus (stat by stat); entries that reach 0 are dropped."""
    out = {(s['id'], s.get('param')): dict(s) for s in stats}
    for s in minus:
        k = (s['id'], s.get('param'))
        if k in out:
            out[k]['value'] -= s['value']
        else:
            out[k] = dict(id=s['id'], param=s.get('param'), value=-s['value'])
    return [s for s in out.values() if s['value'] != 0]

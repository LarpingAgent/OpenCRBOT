from __future__ import annotations


def clock_glyphs(frame, box, cv2):
    height, width = frame.shape[:2]
    x0, y0, x1, y1 = (round(v * dim) for v, dim in zip(box, (width, height, width, height), strict=True))
    mask = (frame[y0:y1, x0:x1].min(axis=2) > 180).astype("uint8")
    if not mask.size:
        return []
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    components = sorted(tuple(row) for row in stats[1:] if row[4] >= 5)
    digits = [(x, y, w, h) for x, y, w, h, _ in components if h >= 0.6 * mask.shape[0]]
    dots = [(x, y) for x, y, w, h, _ in components if w < 0.4 * mask.shape[0] and h < 0.4 * mask.shape[0]]
    if len(digits) != 3 or len(dots) != 2:
        return []
    if not all(digits[0][0] < x < digits[1][0] for x, _ in dots):
        return []
    return [cv2.resize(mask[y:y+h, x:x+w], (16, 24), interpolation=cv2.INTER_NEAREST)
            for x, y, w, h in digits]


def read_clock(frame, box, templates, cv2):
    glyphs = clock_glyphs(frame, box, cv2)
    if not glyphs:
        return None
    digits = []
    for glyph in glyphs:
        ranked = sorted((float((glyph != sample).mean()), digit) for digit, sample in templates.items())
        error, digit = ranked[0]
        if error > 0.15 or ranked[1][0] - error < 0.035:
            return None
        digits.append(int(digit))
    minutes, tens, ones = digits
    if tens >= 6:
        return None
    return minutes * 60 + tens * 10 + ones

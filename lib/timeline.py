"""Frame-grid arithmetic and narrative segments; music remains the only clock."""
from __future__ import annotations

import math


def _number(value, label):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{label} must be finite')
    return value


def quantize(t, fps):
    fps = _number(fps, 'fps')
    if fps <= 0:
        raise ValueError('fps must be positive')
    return round(_number(t, 'time') * fps) / fps


def validate_timeline(scene_plan, song_map, fps):
    """Raise on off-grid boundaries, gaps, overlap, or frame-count mismatch."""
    fps = _number(fps, 'fps')
    if fps <= 0:
        raise ValueError('fps must be positive')
    duration = _number(song_map['audio']['duration_seconds'], 'song duration')
    if duration <= 0:
        raise ValueError('song duration must be positive')
    expected = round(duration * fps)
    scenes = scene_plan['scenes']
    if not scenes:
        raise ValueError('timeline has no scenes')
    cursor, ids = 0, set()
    for scene in scenes:
        if scene['id'] in ids:
            raise ValueError(f"duplicate scene id: {scene['id']}")
        ids.add(scene['id'])
        boundaries = [_number(scene[key], key) * fps for key in ('start_seconds', 'end_seconds')]
        if any(abs(value - round(value)) > 1e-6 for value in boundaries):
            raise ValueError(f"off-grid boundary: {scene['id']}")
        start, end = map(round, boundaries)
        if start != cursor:
            raise ValueError(f"gap or overlap at {scene['id']}: expected frame {cursor}, got {start}")
        if end <= start:
            raise ValueError(f"non-positive scene duration: {scene['id']}")
        cursor = end
    if cursor != expected:
        raise ValueError(f'total frames {cursor} != song frames {expected}')
    return {'valid': True, 'fps': fps, 'total_frames': cursor, 'expected_frames': expected}


def build_segments(song_map, min_s=3, max_s=15):
    """Section edges take precedence; favor lyric edges inside each section.

    Long gaps without lyric boundaries get balanced duration splits. A section
    shorter than min_s stays intact and explicitly records that exception.
    """
    min_s, max_s = _number(min_s, 'min_s'), _number(max_s, 'max_s')
    if not 0 < min_s <= max_s:
        raise ValueError('require 0 < min_s <= max_s')
    fps = _number(song_map.get('metadata', {}).get('fps', 24), 'fps')
    if fps <= 0:
        raise ValueError('fps must be positive')
    minimum, maximum = math.ceil(min_s * fps), math.floor(max_s * fps)
    if minimum > maximum:
        raise ValueError('segment limits contain no valid frame duration')
    lines = song_map.get('lyrics', {}).get('lines', [])
    segments = []
    for section in song_map['sections']:
        left, right = (round(_number(section[k], k) * fps) for k in ('start_seconds', 'end_seconds'))
        length = right - left
        if length <= 0:
            raise ValueError('section collapses on frame grid')
        count = math.ceil(length / maximum)
        if length >= minimum and count * minimum > length:
            raise ValueError('section cannot satisfy min_s/max_s; change segment limits')
        candidates = {round(float(line[k]) * fps) for line in lines
                      for k in ('start_seconds', 'end_seconds') if line.get(k) is not None
                      and math.isfinite(float(line[k]))}
        cursor = left
        for part in range(count):
            remaining = count - part - 1
            if not remaining:
                boundary, reason = right, 'section_boundary'
            else:
                low = max(cursor + minimum, right - remaining * maximum)
                high = min(cursor + maximum, right - remaining * minimum)
                target = round(cursor + (right - cursor) / (remaining + 1))
                valid = [value for value in candidates if low <= value <= high]
                boundary = min(valid, key=lambda value: (abs(value - target), value)) if valid else min(high, max(low, target))
                reason = 'lyric_boundary' if valid else 'duration_limit'
            enclosed = [line['id'] for line in lines if line.get('start_seconds') is not None
                        and line.get('end_seconds') is not None
                        and float(line['end_seconds']) > cursor / fps
                        and float(line['start_seconds']) < boundary / fps]
            segments.append({'id': f'seg-{len(segments)+1:03d}', 'section_id': section['id'],
                'start_seconds': cursor / fps, 'end_seconds': boundary / fps,
                'lyric_line_ids': enclosed, 'boundary_reason': reason,
                **({'constraint_exception': 'section_shorter_than_min_s'} if length < minimum else {})})
            cursor = boundary
    validate_timeline({'scenes': segments}, song_map, fps)
    return segments

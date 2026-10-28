function center(box) {
  return [box[0] + box[2] / 2, box[1] + box[3] / 2];
}

// The model can return both a broad answer-line box and precise boxes for its
// individual answers. Keep the precise ticks on the paper; every check remains
// available in the written review.
export function visiblePaperMarks(markers) {
  const located = markers.filter((marker) => marker.box);
  return located.filter((marker) => {
    if (marker.status !== 'looks_correct' || marker.stepIndex != null || marker.reviewed)
      return true;
    const [x, y, width, height] = marker.box;
    if (width <= 0.28 && height <= 0.14) return true;
    const hasPreciseAnswer = located.some((other) => {
      if (
        other.id === marker.id ||
        other.status !== 'looks_correct' ||
        other.stepIndex != null ||
        other.box[2] >= width * 0.7
      )
        return false;
      const [otherX, otherY] = center(other.box);
      return (
        x - 0.03 <= otherX &&
        otherX <= x + width + 0.03 &&
        y - 0.03 <= otherY &&
        otherY <= y + height + 0.03
      );
    });
    return !hasPreciseAnswer;
  });
}

export function paperMarkEdge(box) {
  return [box[0] + box[2] > 0.91 ? 'edge-right' : '', box[1] + box[3] > 0.92 ? 'edge-bottom' : '']
    .filter(Boolean)
    .join(' ');
}

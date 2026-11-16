import { pageAgreement, projectBox, registerPaper } from './paper-registration.js';

// Intersect line segments with the union of all areas belonging to a question.
// Dense midpoint integration gives consistent results for sparse pointer events.
export function selectQuestion(questions, strokes) {
  const scores = questions
    .filter((q) => q.read_confidence >= 0.85 && q.progress !== 'occluded')
    .map((question) => {
      const regions = [question.box, ...(question.answer_regions || [])];
      const inside = ([x, y]) =>
        regions.some(([bx, by, w, h]) => x >= bx && x <= bx + w && y >= by && y <= by + h);
      let total = 0,
        hit = 0,
        endpoints = 0;
      for (const stroke of strokes) {
        if (stroke.length) endpoints += Number(inside(stroke.at(-1)));
        for (let i = 1; i < stroke.length; i++) {
          const a = stroke[i - 1],
            b = stroke[i],
            length = Math.hypot(b[0] - a[0], b[1] - a[1]);
          const count = Math.max(1, Math.ceil(length * 400));
          total += length;
          for (let j = 0; j < count; j++)
            if (
              inside([
                a[0] + ((b[0] - a[0]) * (j + 0.5)) / count,
                a[1] + ((b[1] - a[1]) * (j + 0.5)) / count,
              ])
            )
              hit += length / count;
        }
      }
      return { question, fraction: total ? hit / total : 0, endpoints };
    })
    .filter((entry) => entry.fraction >= 0.25)
    .sort((a, b) => b.fraction - a.fraction || b.endpoints - a.endpoints);
  const winner = scores[0];
  const certain =
    winner &&
    winner.fraction >= 0.65 &&
    (!scores[1] || winner.fraction - scores[1].fraction >= 0.16);
  return {
    question: certain ? winner.question : null,
    candidates: scores.slice(0, 4).map((entry) => entry.question),
  };
}

export function bindPaperIndex(index, currentPixels, strokes) {
  if (!index?.pixels || !index.questions?.length) return null;
  const registration = registerPaper(index.pixels, currentPixels);
  if (!registration) return null;
  const { matrix } = registration;
  const questions = index.questions.flatMap((question) => {
    const box = projectBox(matrix, question.box);
    if (!box || !pageAgreement(index.pixels, currentPixels, matrix, question.box).reliable)
      return [];
    const reading = question.reading && {
      ...question.reading,
      page: question.reading.page.flatMap((mark) => {
        const mapped = mark.box && projectBox(matrix, mark.box);
        return mapped ? [{ ...mark, box: mapped }] : [];
      }),
    };
    return [
      {
        ...question,
        box,
        reading,
        answer_regions: (question.answer_regions || [])
          .map((region) => projectBox(matrix, region))
          .filter(Boolean),
      },
    ];
  });
  return {
    ...selectQuestion(questions, strokes),
    geometry: {
      page_id: index.page_id,
      frame_sha256: index.frame_sha256,
      matrix,
    },
    method: registration.method,
  };
}

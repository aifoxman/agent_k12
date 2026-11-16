import { bindPaperIndex } from './paper-index.js';

self.onmessage = ({ data }) => {
  try {
    self.postMessage({ binding: bindPaperIndex(data.index, data.pixels, data.strokes) });
  } catch {
    self.postMessage({ binding: null });
  }
};

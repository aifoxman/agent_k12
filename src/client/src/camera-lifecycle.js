// A system USB permission sheet can hide the WebView before its first frame.
// Existing streams and real background notifications still close normally.
export function keepPendingCameraPermission({ stage, opening, hasStream, nativeStatus }) {
  if (!['preview', 'setup'].includes(stage) || !opening || hasStream) return false;
  try {
    const status = typeof nativeStatus === 'string' ? JSON.parse(nativeStatus) : nativeStatus;
    return status?.attached === true && status.state === 'permission';
  } catch {
    return false;
  }
}

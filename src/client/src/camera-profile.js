// UVC canvas pixels already have the selected sensor resolution and orientation.
// Applying a builtin-camera 4:3 constraint here crops portrait paper captures.
export async function applyPaperCaptureProfile(source, highResolution = false) {
  if (!source || source.kind === 'uvc') return;
  const track = source.stream?.getVideoTracks()[0];
  if (!track?.applyConstraints) return;
  await track.applyConstraints({
    width: { ideal: highResolution ? 1600 : 1280 },
    height: { ideal: highResolution ? 1200 : 960 },
    aspectRatio: { ideal: 4 / 3 },
    ...(highResolution ? { resizeMode: { exact: 'none' } } : {}),
  });
}

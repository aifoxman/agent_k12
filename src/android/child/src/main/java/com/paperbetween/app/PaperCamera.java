package com.paperbetween.app;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbManager;
import android.net.Uri;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import com.serenegiant.opengl.renderer.RendererHolder;
import com.serenegiant.usb.Size;
import com.serenegiant.usb.USBMonitor;
import com.serenegiant.usb.USBMonitor.UsbControlBlock;
import com.serenegiant.usb.USBMonitor.USBException;
import com.serenegiant.usb.UVCCamera;
import com.serenegiant.usb.UVCControl;
import com.serenegiant.usb.UVCParam;
import com.serenegiant.utils.UVCUtils;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.Comparator;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONException;
import org.json.JSONArray;
import org.json.JSONObject;

/** UVC video only. Frames stay in memory and are never routed through the JS bridge. */
public final class PaperCamera implements NativeCamera {
    private static final long MAX_FRAME_AGE_MS = 2500;
    private static final long JPEG_INTERVAL_MS = 100;
    private final Activity activity;
    private final WebView web;
    private final UsbManager usb;
    private final Object lock = new Object();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final HandlerThread cameraThread;
    private final Handler worker;
    private final ExecutorService encoder = Executors.newSingleThreadExecutor();
    private final AtomicBoolean encoding = new AtomicBoolean();
    private final String instanceId = UUID.randomUUID().toString();
    private Session session;
    private UsbDevice selected;
    private boolean wanted;
    private boolean foreground = true;
    private boolean destroyed;
    private boolean cameraPermissionPending;
    private boolean usbPermissionPending;
    private boolean deferredOpen;
    private long generation;
    private long frameSequence;
    private long lastEncodeMs;
    private String state = "idle";
    private String error = "";
    private String errorCode = "";
    private Frame latest;
    private JSONArray supportedModes = new JSONArray();
    private JSONObject selectedMode;
    private Boolean focusAutoSupported;
    private Boolean focusAuto;

    /** Resource fields are read and written only on the camera worker. */
    private static final class Session {
        final long generation;
        final UsbDevice device;
        USBMonitor monitor;
        UsbControlBlock original;
        UVCCamera camera;
        RendererHolder renderer;
        final UvcModeFallback.Attempts attempts;
        List<Size> previewModes;
        volatile Runnable openingTimeout, firstFrameTimeout, focusTask;
        volatile boolean released;
        Session(long generation, UsbDevice device) {
            this.generation = generation;
            this.device = device;
            attempts = new UvcModeFallback.Attempts(generation);
        }
    }

    private static final class Frame {
        final byte[] jpeg;
        final int width, height;
        final long generation, id, capturedAt;
        Frame(byte[] jpeg, int width, int height, long generation, long id, long capturedAt) {
            this.jpeg = jpeg;
            this.width = width;
            this.height = height;
            this.generation = generation;
            this.id = id;
            this.capturedAt = capturedAt;
        }
    }

    public PaperCamera(Activity activity, WebView web) {
        this.activity = activity;
        this.web = web;
        usb = (UsbManager) activity.getSystemService(Activity.USB_SERVICE);
        UVCUtils.init(activity.getApplication());
        cameraThread = new HandlerThread("paper-uvc-control");
        cameraThread.start();
        worker = new Handler(cameraThread.getLooper());
    }

    private UsbDevice attachedDevice() {
        if (usb == null) return null;
        return usb.getDeviceList().values().stream().filter(PaperCamera::isVideoDevice)
            .min(Comparator.comparing(UsbDevice::getDeviceName)).orElse(null);
    }

    private static boolean isVideoDevice(UsbDevice device) {
        if (device.getDeviceClass() == UsbConstants.USB_CLASS_VIDEO) return true;
        for (int i = 0; i < device.getInterfaceCount(); i++)
            if (device.getInterface(i).getInterfaceClass() == UsbConstants.USB_CLASS_VIDEO) return true;
        return false;
    }

    private boolean active(long token) {
        synchronized (lock) { return !destroyed && wanted && token == generation; }
    }

    @JavascriptInterface @Override public String open() {
        final long token;
        synchronized (lock) {
            if (destroyed || wanted) return status();
            wanted = true;
            token = ++generation;
            latest = null;
            supportedModes = new JSONArray();
            selectedMode = null;
            focusAutoSupported = null;
            focusAuto = null;
            lastEncodeMs = 0;
            error = "";
            errorCode = "";
            state = "opening";
        }
        main.post(() -> openOnMain(token));
        return status();
    }

    private void openOnMain(long token) {
        if (!active(token)) return;
        synchronized (lock) {
            if (!foreground) {
                fail(token, "background", "请回到应用后重新打开 USB 相机");
                return;
            }
        }
        if (attachedDevice() == null) {
            fail(token, "no_device", "未发现外接 USB 相机，请检查连接");
            return;
        }
        if (activity.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            synchronized (lock) { cameraPermissionPending = true; state = "permission"; }
            emit();
            activity.requestPermissions(new String[]{Manifest.permission.CAMERA}, 44);
            return;
        }
        worker.post(() -> startMonitor(token));
    }

    private void startMonitor(long token) {
        if (!active(token)) return;
        UsbDevice device = attachedDevice();
        if (device == null) { fail(token, "disconnected", "USB 相机已断开"); return; }
        Session current = new Session(token, device);
        synchronized (lock) {
            if (!active(token)) return;
            session = current;
            selected = device;
            usbPermissionPending = !usb.hasPermission(device);
            state = usbPermissionPending ? "permission" : "opening";
        }
        try {
            current.monitor = new USBMonitor(activity.getApplicationContext(), new USBMonitor.OnDeviceConnectListener() {
                @Override public void onAttach(UsbDevice ignored) { }
                @Override public void onDeviceOpen(UsbDevice opened, UsbControlBlock original, boolean firstOpen) {
                    if (!active(token) || !device.equals(opened) || current.released) {
                        closeBlock(original);
                        return;
                    }
                    current.original = original;
                    synchronized (lock) {
                        usbPermissionPending = false;
                        state = "opening";
                        deferredOpen = !foreground;
                        if (deferredOpen) return;
                    }
                    startNative(current);
                }
                @Override public void onDeviceClose(UsbDevice closed, UsbControlBlock original) {
                    if (active(token) && device.equals(closed)) fail(token, "disconnected", "USB 相机已断开");
                }
                @Override public void onDetach(UsbDevice detached) {
                    if (active(token) && device.equals(detached))
                        fail(token, "disconnected", "USB 相机已拔出，请重新连接");
                }
                @Override public void onCancel(UsbDevice cancelled) {
                    if (active(token) && device.equals(cancelled))
                        fail(token, "permission_denied", "USB 相机权限未获允许，请再次打开并授权");
                }
                @Override public void onError(UsbDevice failed, USBException exception) {
                    if (device.equals(failed)) fail(token, "camera_error", "USB 相机打开失败，请重新连接后重试");
                }
            }, worker);
            current.monitor.register();
            current.monitor.requestPermission(device);
            scheduleOpeningTimeout(current);
            emit();
        } catch (RuntimeException | LinkageError exception) {
            fail(token, "camera_error", "USB 相机初始化失败，请重新连接后重试");
        }
    }

    private void startNative(Session current) {
        long token = current.generation;
        synchronized (lock) {
            if (!active(token) || current.released || current.original == null || current.camera != null) return;
            if (!foreground) { deferredOpen = true; return; }
        }
        scheduleOpeningTimeout(current);
        try {
            // The low-level API has no recorder and never requests or opens a microphone.
            current.camera = new UVCCamera(new UVCParam());
            if (current.camera.open(current.original) != 0) {
                fail(token, "camera_error", "USB 相机打开失败，请重新连接后重试");
                return;
            }
            List<Size> sizes = current.camera.getSupportedSizeList();
            JSONArray modes = new JSONArray();
            if (sizes != null) for (Size supported : sizes) modes.put(modeJson(supported));
            synchronized (lock) { if (active(token)) supportedModes = modes; }
            List<UvcModeFallback.Mode> published = new ArrayList<>();
            if (sizes != null) for (int index = 0; index < sizes.size(); index++) {
                Size mode = sizes.get(index);
                if (mode.type == UVCCamera.UVC_VS_FRAME_MJPEG
                    || mode.type == UVCCamera.UVC_VS_FRAME_UNCOMPRESSED)
                    published.add(new UvcModeFallback.Mode(index, mode.width, mode.height, mode.fps,
                        mode.type == UVCCamera.UVC_VS_FRAME_MJPEG));
            }
            current.previewModes = new ArrayList<>();
            for (int index : UvcModeFallback.candidates(published)) current.previewModes.add(sizes.get(index).clone());
            if (current.previewModes.isEmpty()) {
                fail(token, "resolution_unsupported", "USB 相机未提供至少 1280 像素宽的可用画面");
                return;
            }
            startPreviewAttempt(current, 0);
        } catch (RuntimeException | LinkageError exception) {
            fail(token, "camera_error", "USB 相机初始化失败，请重新连接后重试");
        }
    }

    private boolean currentAttempt(Session current, int attempt) {
        synchronized (lock) {
            return session == current && foreground && !current.released && active(current.generation)
                && current.attempts.accepts(current.generation, attempt);
        }
    }

    /** Every mode change stays on the same worker, camera and owned USB connection. */
    private void startPreviewAttempt(Session current, int modeIndex) {
        long token = current.generation;
        synchronized (lock) {
            if (session != current || !active(token) || !foreground || current.released || latest != null) return;
        }
        if (modeIndex >= current.previewModes.size()) {
            fail(token, "frame_timeout", "USB 相机已尝试公布的视频模式，仍未返回有效画面，请检查连接");
            return;
        }
        int attempt = current.attempts.begin();
        if (attempt == 0) return;
        clearAttemptTimers(current);
        try {
            if (modeIndex > 0) {
                current.camera.setFrameCallback(null, UVCCamera.PIXEL_FORMAT_NV21);
                current.camera.stopPreview();
                if (current.renderer != null) {
                    current.renderer.release();
                    current.renderer = null;
                }
            }
            if (!currentAttempt(current, attempt)) return;
            synchronized (lock) { lastEncodeMs = 0; }
            current.camera.setPreviewSize(current.previewModes.get(modeIndex));
            Size actualSize = current.camera.getPreviewSize().clone();
            synchronized (lock) { if (active(token)) selectedMode = modeJson(actualSize); }
            // Native UVCPreview requires an ANativeWindow even for frame callbacks.
            // This public renderer drains an offscreen texture; no native view is displayed.
            current.renderer = new RendererHolder(actualSize.width, actualSize.height, null);
            current.camera.setPreviewDisplay(current.renderer.getPrimarySurface());
            current.camera.setFrameCallback(buffer -> receiveFrame(current, attempt, actualSize, buffer), UVCCamera.PIXEL_FORMAT_NV21);
            if (!currentAttempt(current, attempt)) return;
            current.camera.startPreview();
            synchronized (lock) {
                if (!currentAttempt(current, attempt)) return;
                current.focusTask = () -> configureFocus(current, attempt);
                worker.postDelayed(current.focusTask, 500);
                current.firstFrameTimeout = () -> worker.post(() -> {
                    synchronized (lock) {
                        if (!currentAttempt(current, attempt) || latest != null) return;
                    }
                    android.util.Log.w("PaperCamera", "No first frame from published mode " + actualSize);
                    startPreviewAttempt(current, modeIndex + 1);
                });
                main.postDelayed(current.firstFrameTimeout, UvcModeFallback.FIRST_FRAME_TIMEOUT_MS);
            }
            emit();
        } catch (IllegalArgumentException rejectedMode) {
            android.util.Log.w("PaperCamera", "Published UVC mode rejected, attempt " + attempt);
            startPreviewAttempt(current, modeIndex + 1);
        } catch (RuntimeException | LinkageError exception) {
            fail(token, "camera_error", "USB 相机初始化失败，请重新连接后重试");
        }
    }

    private static JSONObject modeJson(Size size) {
        JSONObject result = new JSONObject();
        try {
            result.put("width", size.width);
            result.put("height", size.height);
            result.put("fps", size.fps);
            result.put("format", size.type == UVCCamera.UVC_VS_FRAME_MJPEG ? "MJPEG"
                : size.type == UVCCamera.UVC_VS_FRAME_UNCOMPRESSED ? "YUYV" : "other");
            result.put("fps_list", size.fpsList == null ? new JSONArray() : new JSONArray(size.fpsList));
        } catch (JSONException impossible) { throw new IllegalStateException(impossible); }
        return result;
    }

    private void configureFocus(Session current, int attempt) {
        synchronized (lock) {
            if (!currentAttempt(current, attempt) || current.camera == null) return;
        }
        try {
            UVCControl control = current.camera.getControl();
            Boolean supported = control == null ? null : control.isFocusAutoEnable();
            Boolean enabled = null;
            if (Boolean.TRUE.equals(supported)) {
                control.setFocusAuto(true);
                enabled = control.getFocusAuto();
            }
            synchronized (lock) {
                if (!currentAttempt(current, attempt)) return;
                focusAutoSupported = supported;
                focusAuto = enabled;
            }
            emit();
        } catch (RuntimeException | LinkageError exception) {
            // An unsupported or unreadable focus control must not break otherwise valid video.
            android.util.Log.w("PaperCamera", "USB autofocus could not be confirmed", exception);
        }
    }

    private void clearAttemptTimers(Session current) {
        synchronized (lock) {
            if (current.firstFrameTimeout != null) main.removeCallbacks(current.firstFrameTimeout);
            if (current.focusTask != null) worker.removeCallbacks(current.focusTask);
            current.firstFrameTimeout = null;
            current.focusTask = null;
        }
    }

    private void clearTimers(Session current) {
        clearAttemptTimers(current);
        synchronized (lock) {
            if (current.openingTimeout != null) main.removeCallbacks(current.openingTimeout);
            current.openingTimeout = null;
        }
    }

    private void scheduleOpeningTimeout(Session current) {
        synchronized (lock) {
            if (session != current || !active(current.generation)) return;
            if (current.openingTimeout != null) main.removeCallbacks(current.openingTimeout);
            current.openingTimeout = () -> {
            synchronized (lock) {
                if (session != current || !active(current.generation) || latest != null
                    || cameraPermissionPending || usbPermissionPending) return;
            }
            fail(current.generation, "frame_timeout", "USB 相机未返回有效画面，请重新连接");
            };
            main.postDelayed(current.openingTimeout, UvcModeFallback.OPEN_TIMEOUT_MS);
        }
    }

    private void receiveFrame(Session current, int attempt, Size size, ByteBuffer buffer) {
        long token = current.generation;
        long capturedAt = SystemClock.elapsedRealtime();
        synchronized (lock) {
            if (!currentAttempt(current, attempt) || capturedAt - lastEncodeMs < JPEG_INTERVAL_MS) return;
        }
        if (!encoding.compareAndSet(false, true)) return;
        boolean submitted = false;
        try {
            if (size == null || size.width < 1280 || size.width > 1920 || size.height <= 0 || size.height > 1440) return;
            int bytes = size.width * size.height * 3 / 2;
            ByteBuffer copy = buffer.duplicate();
            copy.rewind();
            if (copy.remaining() < bytes) return;
            byte[] nv21 = new byte[bytes];
            copy.get(nv21);
            synchronized (lock) { lastEncodeMs = capturedAt; }
            encoder.execute(() -> {
                try {
                    ByteArrayOutputStream output = new ByteArrayOutputStream();
                    boolean valid = new YuvImage(nv21, ImageFormat.NV21, size.width, size.height, null)
                        .compressToJpeg(new Rect(0, 0, size.width, size.height), 85, output);
                    if (!valid) return;
                    boolean first;
                    synchronized (lock) {
                        if (!currentAttempt(current, attempt)) return;
                        first = latest == null;
                        latest = new Frame(output.toByteArray(), size.width, size.height, token, ++frameSequence, capturedAt);
                        state = "streaming";
                    }
                    if (first) {
                        synchronized (lock) {
                            if (current.firstFrameTimeout != null) main.removeCallbacks(current.firstFrameTimeout);
                            if (current.openingTimeout != null) main.removeCallbacks(current.openingTimeout);
                            current.firstFrameTimeout = null;
                            current.openingTimeout = null;
                        }
                        emit();
                    }
                } catch (RuntimeException exception) {
                    if (currentAttempt(current, attempt))
                        fail(token, "frame_encoding", "USB 相机画面无法解码，请重新连接");
                } finally { encoding.set(false); }
            });
            submitted = true;
            return;
        } catch (RejectedExecutionException ignored) {
            // An already closed activity cannot accept another frame.
        } finally {
            // Submitted work owns this flag until JPEG compression finishes.
            if (!submitted) encoding.set(false);
        }
    }

    @JavascriptInterface @Override public String status() {
        UsbDevice present = attachedDevice();
        synchronized (lock) {
            if (selected != null) present = usb.getDeviceList().get(selected.getDeviceName());
            long age = latest == null ? -1 : Math.max(0, SystemClock.elapsedRealtime() - latest.capturedAt);
            String currentState = "streaming".equals(state) && age > MAX_FRAME_AGE_MS ? "stale" : state;
            String deviceName = "";
            if (present != null) {
                try { deviceName = present.getProductName(); } catch (SecurityException ignored) { }
                if (deviceName == null || deviceName.isEmpty()) deviceName = "USB camera";
            }
            JSONObject result = new JSONObject();
            try {
                result.put("supported", usb != null && !destroyed);
                result.put("source", "uvc");
                result.put("attached", present != null);
                result.put("permission", present != null && usb.hasPermission(present)
                    && activity.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED);
                result.put("state", currentState);
                result.put("generation", generation);
                result.put("session_id", instanceId + "-" + generation);
                result.put("device_name", deviceName);
                result.put("width", latest == null ? 0 : latest.width);
                result.put("height", latest == null ? 0 : latest.height);
                result.put("frame_id", latest == null ? 0 : latest.id);
                result.put("captured_at_ms", latest == null ? 0 : latest.capturedAt);
                result.put("frame_age_ms", age);
                result.put("frame_url", "/native-camera/frame.jpg?generation=" + generation);
                result.put("error", error);
                result.put("error_code", errorCode);
                result.put("supported_modes", supportedModes);
                result.put("selected_mode", selectedMode == null ? JSONObject.NULL : selectedMode);
                result.put("focus_auto_supported", focusAutoSupported == null ? JSONObject.NULL : focusAutoSupported);
                result.put("focus_auto", focusAuto == null ? JSONObject.NULL : focusAuto);
            } catch (JSONException impossible) { throw new IllegalStateException(impossible); }
            return result.toString();
        }
    }

    @Override public WebResourceResponse frame(Uri uri) {
        Frame frame;
        long currentGeneration;
        synchronized (lock) {
            currentGeneration = generation;
            if (!Long.toString(generation).equals(uri.getQueryParameter("generation")))
                return response(409, "Conflict", null, generation);
            frame = latest;
            if (!wanted || !foreground || destroyed || frame == null || frame.generation != generation
                || SystemClock.elapsedRealtime() - frame.capturedAt > MAX_FRAME_AGE_MS || selected == null
                || !usb.getDeviceList().containsKey(selected.getDeviceName()))
                return response(503, "Service Unavailable", null, generation);
        }
        return response(200, "OK", frame, currentGeneration);
    }

    private WebResourceResponse response(int code, String reason, Frame frame, long token) {
        Map<String, String> headers = new HashMap<>();
        headers.put("Cache-Control", "no-store, no-cache, must-revalidate");
        headers.put("Pragma", "no-cache");
        headers.put("X-Content-Type-Options", "nosniff");
        headers.put("X-Paper-Camera-Generation", Long.toString(token));
        if (frame != null) {
            headers.put("X-Paper-Camera-Frame-Id", Long.toString(frame.id));
            headers.put("X-Paper-Camera-Captured-At-Ms", Long.toString(frame.capturedAt));
        }
        return new WebResourceResponse(frame == null ? "text/plain" : "image/jpeg", null, code, reason,
            headers, new ByteArrayInputStream(frame == null ? new byte[0] : frame.jpeg));
    }

    @JavascriptInterface @Override public String close() {
        stop("idle", "", "");
        return status();
    }

    private void fail(long token, String code, String message) {
        synchronized (lock) {
            if (!active(token)) return;
            stop("disconnected".equals(code) ? "disconnected" : "error", code, message);
        }
    }

    private void stop(String nextState, String code, String message) {
        Session old;
        synchronized (lock) {
            if (wanted || session != null || latest != null || !state.equals(nextState)) generation++;
            wanted = false;
            cameraPermissionPending = false;
            usbPermissionPending = false;
            deferredOpen = false;
            latest = null;
            supportedModes = new JSONArray();
            selectedMode = null;
            focusAutoSupported = null;
            focusAuto = null;
            old = session;
            session = null;
            selected = null;
            state = nextState;
            errorCode = code;
            error = message;
        }
        if (old != null) {
            old.attempts.close();
            clearTimers(old);
            worker.post(() -> releaseSession(old));
        }
        emit();
    }

    private static void cleanup(String phase, Runnable action) {
        try { action.run(); }
        catch (RuntimeException | LinkageError exception) {
            android.util.Log.w("PaperCamera", "USB camera cleanup failed: " + phase, exception);
        }
    }

    private static void closeBlock(UsbControlBlock block) {
        if (block == null) return;
        UsbDeviceConnection connection = block.getConnection();
        try { cleanup("control block", () -> block.close(true)); }
        finally {
            // releaseInterface may fail after physical removal; still close the owned descriptor.
            if (connection != null) cleanup("device connection", connection::close);
        }
    }

    /** Runs on the camera worker after all earlier native operations. */
    private static void releaseSession(Session current) {
        if (current.released) return;
        current.released = true;
        if (current.camera != null) {
            UsbControlBlock cloned = current.camera.getUsbControlBlock();
            cleanup("frame callback", () -> current.camera.setFrameCallback(null, UVCCamera.PIXEL_FORMAT_NV21));
            cleanup("preview", current.camera::stopPreview);
            cleanup("native camera", current.camera::destroy);
            closeBlock(cloned);
            current.camera = null;
        }
        // UVCCamera closes its clone, which removes the monitor's map entry by device.
        // The original block is a distinct owned connection and must be closed explicitly.
        closeBlock(current.original);
        current.original = null;
        if (current.monitor != null) {
            cleanup("USB monitor", current.monitor::destroy);
            current.monitor = null;
        }
        if (current.renderer != null) {
            cleanup("offscreen renderer", current.renderer::release);
            current.renderer = null;
        }
    }

    private void emit() {
        main.post(() -> {
            synchronized (lock) { if (destroyed) return; }
            web.evaluateJavascript("window.dispatchEvent(new CustomEvent('paper-camera-state',{detail:"
                + status() + "}))", null);
        });
    }

    @Override public boolean permissionPending() {
        synchronized (lock) { return cameraPermissionPending || usbPermissionPending; }
    }

    @Override public void onCameraPermissionResult(boolean granted) {
        long token;
        synchronized (lock) {
            if (!wanted || !cameraPermissionPending) return;
            token = generation;
            cameraPermissionPending = false;
            if (granted && !foreground) { deferredOpen = true; return; }
        }
        if (granted) openOnMain(token);
        else fail(token, "permission_denied", "相机权限未获允许，请再次打开并授权");
    }

    @Override public void onActivityPause() {
        synchronized (lock) {
            foreground = false;
            latest = null;
            // System permission UI holds only the monitor, never an active UVC stream.
            if (cameraPermissionPending || usbPermissionPending) return;
        }
        stop("idle", "background", "应用已暂停，USB 相机已释放");
    }

    @Override public void onActivityResume() {
        Session current;
        long token;
        synchronized (lock) {
            foreground = true;
            if (!wanted || !deferredOpen) return;
            deferredOpen = false;
            current = session;
            token = generation;
        }
        if (current != null) worker.post(() -> startNative(current));
        else openOnMain(token);
    }

    @Override public void onActivityStop() {
        stop("idle", "background", "应用已离开前台，USB 相机已释放");
    }

    @Override public void destroy() {
        synchronized (lock) { destroyed = true; }
        stop("idle", "", "");
        encoder.shutdownNow();
        worker.post(cameraThread::quitSafely);
    }
}

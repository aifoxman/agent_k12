package com.paperbetween.app;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.content.pm.ApplicationInfo;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;
import android.util.Base64;
import android.view.ActionMode;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Locale;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

public final class MainActivity extends Activity {
    private static final float DEFAULT_SPEECH_RATE = 0.95f;
    private WebView web;
    private PermissionRequest pendingWebPermission;
    private SpeechRecognizer recognizer;
    private TextToSpeech tts;
    private volatile PcmPlayer pcmPlayer;
    private AutoCloseable paperVision;
    private NativeCamera paperCamera;
    private boolean pendingVoice;
    private boolean recognitionWanted;
    private boolean holdRecognition;
    private boolean deferHoldUntilRelease;
    private boolean recognizerWaiting;
    private boolean retriedWithDefaultRecognizer;
    private boolean recognizerOnDevice;
    private int recognitionGeneration;
    private long recognitionStartedAtMs;
    private final ArrayList<String> holdWords = new ArrayList<>();
    private boolean recognitionOutputOverlap;
    private volatile boolean holdGestureActive;
    private final String origin = "https://appassets.androidplatform.net";

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        web.setOnLongClickListener(view -> holdGestureActive);
        setContentView(web);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        boolean debug = (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0;
        settings.setMixedContentMode(debug ? WebSettings.MIXED_CONTENT_ALWAYS_ALLOW : WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        WebView.setWebContentsDebuggingEnabled(debug);
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                String urlOrigin = request.getUrl().getScheme() + "://" + request.getUrl().getHost();
                String path = request.getUrl().getPath();
                if (origin.equals(urlOrigin) && "/native-camera/frame.jpg".equals(path)) {
                    if (paperCamera == null || !"GET".equals(request.getMethod())) return missingAsset();
                    return paperCamera.frame(request.getUrl());
                }
                if (!origin.equals(urlOrigin) || !path.startsWith("/app/")) return null;
                String asset = path.substring(5);
                if (asset.isEmpty() || asset.contains("..") || asset.startsWith("/")) return missingAsset();
                try {
                    String type = asset.endsWith(".html") ? "text/html" :
                        asset.endsWith(".js") ? "application/javascript" :
                        asset.endsWith(".css") ? "text/css" :
                        asset.endsWith(".svg") ? "image/svg+xml" :
                        asset.endsWith(".png") ? "image/png" :
                        asset.endsWith(".woff2") ? "font/woff2" : "application/octet-stream";
                    return new WebResourceResponse(type, "UTF-8", getAssets().open(asset));
                } catch (IOException error) { return missingAsset(); }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return !origin.equals(request.getUrl().getScheme() + "://" + request.getUrl().getHost());
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> handleWebPermission(request));
            }
        });
        web.addJavascriptInterface(new NativeVoice(), "PaperVoice");
        tts = new TextToSpeech(this, status -> { if (status == TextToSpeech.SUCCESS && tts != null) tts.setLanguage(Locale.SIMPLIFIED_CHINESE); });
        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override public void onStart(String utteranceId) { callbackSpeechOutputStart(utteranceId); }
            @Override public void onDone(String utteranceId) { callbackSpeechOutputDone(utteranceId); }
            @Override public void onError(String utteranceId) { callbackSpeechOutputDone(utteranceId); }
        });
        String mode = getPackageName().endsWith(".child") ? "child" : "parent";
        if (mode.equals("child")) {
            getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            try {
                paperCamera = (NativeCamera) Class.forName("com.paperbetween.app.PaperCamera")
                    .getConstructor(Activity.class, WebView.class).newInstance(this, web);
                web.addJavascriptInterface(paperCamera, "PaperCamera");
            } catch (ReflectiveOperationException | LinkageError error) {
                android.util.Log.w("PaperCamera", "USB camera bridge unavailable", error);
            }
            try {
                // The child module owns the bundled OCR dependency; the parent APK stays small.
                paperVision = (AutoCloseable) Class.forName("com.paperbetween.app.PaperVision")
                    .getConstructor(WebView.class).newInstance(web);
                web.addJavascriptInterface(paperVision, "PaperVision");
            } catch (ReflectiveOperationException error) {
                android.util.Log.w("PaperVision", "Local OCR unavailable", error);
            }
        }
        web.loadUrl(origin + "/app/index.html?mode=" + mode);
    }

    private WebResourceResponse missingAsset() {
        return new WebResourceResponse("text/plain", "UTF-8", 404, "Not Found",
            java.util.Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
    }

    @Override public void onActionModeStarted(ActionMode mode) {
        if (holdGestureActive) {
            mode.finish();
            return;
        }
        super.onActionModeStarted(mode);
    }

    private void handleWebPermission(PermissionRequest request) {
        if (!origin.equals(request.getOrigin().toString().replaceAll("/$", ""))) { request.deny(); return; }
        for (String resource : request.getResources()) {
            if (!PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource) &&
                !PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) { request.deny(); return; }
        }
        boolean camera = false, microphone = false;
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)) camera = true;
            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) microphone = true;
        }
        ArrayList<String> missing = new ArrayList<>();
        if (camera && checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) missing.add(Manifest.permission.CAMERA);
        if (microphone && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) missing.add(Manifest.permission.RECORD_AUDIO);
        if (missing.isEmpty()) request.grant(request.getResources());
        else { pendingWebPermission = request; requestPermissions(missing.toArray(new String[0]), 42); }
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(code, permissions, grants);
        boolean allowed = grants.length > 0;
        for (int grant : grants) allowed &= grant == PackageManager.PERMISSION_GRANTED;
        if (code == 42 && pendingWebPermission != null) {
            if (allowed) pendingWebPermission.grant(pendingWebPermission.getResources());
            else pendingWebPermission.deny();
            pendingWebPermission = null;
        }
        if (code == 43 && pendingVoice) {
            pendingVoice = false;
            if (allowed) startRecognition();
            else if (!shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO)) {
                callbackVoice("", "请在系统设置中允许麦克风，然后返回应用重试");
                startActivity(new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    android.net.Uri.parse("package:" + getPackageName())));
            } else callbackVoice("", "麦克风权限被拒绝，请再次点击并授权");
        }
        if (code == 44 && paperCamera != null) paperCamera.onCameraPermissionResult(allowed);
    }

    private void callbackVoice(String text, String error) { callbackVoice(text, error, ""); }

    private void callbackVoice(String text, String error, String reason) {
        runOnUiThread(() -> web.evaluateJavascript("window.__nativeSpeechResult && window.__nativeSpeechResult(" +
            org.json.JSONObject.quote(text) + "," + org.json.JSONObject.quote(error) + "," +
            org.json.JSONObject.quote(reason) + ")", null));
    }

    private void callbackSpeechState(String state) {
        runOnUiThread(() -> web.evaluateJavascript("window.__nativeSpeechState && window.__nativeSpeechState(" +
            org.json.JSONObject.quote(state) + ")", null));
    }

    private void callbackSpeechPartial(String text) {
        runOnUiThread(() -> web.evaluateJavascript("window.__nativeSpeechPartial && window.__nativeSpeechPartial(" +
            org.json.JSONObject.quote(text) + ")", null));
    }

    private void finishHold(String error, String reason) {
        if (!holdRecognition) return;
        String text = android.text.TextUtils.join(" ", holdWords);
        holdWords.clear();
        holdRecognition = false;
        recognitionWanted = false;
        recognizerWaiting = false;
        callbackVoice(text, error, reason);
    }

    private void callbackSpeechOutputDone(String utteranceId) {
        runOnUiThread(() -> web.evaluateJavascript("window.__speechOutputDone && window.__speechOutputDone(" +
            org.json.JSONObject.quote(utteranceId) + ")", null));
    }

    private void callbackSpeechOutputStart(String utteranceId) {
        runOnUiThread(() -> web.evaluateJavascript("window.__speechOutputStart && window.__speechOutputStart(" +
            org.json.JSONObject.quote(utteranceId) + ")", null));
    }

    private void stopSystemSpeech() {
        if (tts != null) {
            tts.stop();
            tts.setSpeechRate(DEFAULT_SPEECH_RATE);
        }
    }

    private void callbackPcmOutputDone(String utteranceId, boolean success) {
        runOnUiThread(() -> web.evaluateJavascript("window.__pcmOutputDone && window.__pcmOutputDone(" +
            org.json.JSONObject.quote(utteranceId) + "," + success + ")", null));
    }

    private final class PcmPlayer {
        private final String utteranceId;
        private final AudioTrack track;
        private final LinkedBlockingQueue<byte[]> chunks = new LinkedBlockingQueue<>();
        private final AtomicBoolean reported = new AtomicBoolean();
        private final AtomicInteger bufferedBytes = new AtomicInteger();
        private final byte[] end = new byte[0];
        private volatile boolean stopped;
        private volatile boolean finished;
        private volatile boolean playing;

        PcmPlayer(String id) {
            utteranceId = id;
            int minimum = AudioTrack.getMinBufferSize(24000, AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
            if (minimum <= 0) throw new IllegalStateException("PCM output unavailable");
            track = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                .setAudioFormat(new AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(24000)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                .setTransferMode(AudioTrack.MODE_STREAM)
                .setBufferSizeInBytes(Math.max(minimum, 48000))
                .build();
            if (track.getState() != AudioTrack.STATE_INITIALIZED) {
                track.release();
                throw new IllegalStateException("PCM output failed to initialize");
            }
        }

        void start() { new Thread(this::play, "paper-pcm-output").start(); }

        boolean push(String encoded) {
            if (stopped || finished || chunks.size() >= 512) return false;
            try {
                byte[] bytes = Base64.decode(encoded, Base64.DEFAULT);
                if (bytes.length == 0 || (bytes.length & 1) != 0 || bytes.length > 262144) return false;
                if (bufferedBytes.addAndGet(bytes.length) > 8 * 1024 * 1024) {
                    bufferedBytes.addAndGet(-bytes.length);
                    return false;
                }
                playing = true;
                return chunks.offer(bytes);
            } catch (IllegalArgumentException error) { return false; }
        }

        void finish() {
            if (stopped || finished) return;
            finished = true;
            chunks.offer(end);
        }

        void stop() {
            stopped = true;
            chunks.clear();
            bufferedBytes.set(0);
            chunks.offer(end);
            try { track.pause(); track.flush(); } catch (IllegalStateException ignored) {}
            report(false);
        }

        void report(boolean success) {
            if (reported.compareAndSet(false, true)) callbackPcmOutputDone(utteranceId, success);
        }

        void play() {
            int frames = 0;
            try {
                // Hold about half a second of PCM before starting. Tencent may
                // deliver its first few frames in bursts; this absorbs brief
                // network gaps without a noticeable pause in the teacher voice.
                long prebufferDeadline = System.currentTimeMillis() + 700;
                while (!stopped && !finished && bufferedBytes.get() < 24000
                    && System.currentTimeMillis() < prebufferDeadline)
                    Thread.sleep(10);
                if (stopped) return;
                track.play();
                while (!stopped) {
                    byte[] bytes = chunks.take();
                    if (bytes == end) break;
                    bufferedBytes.updateAndGet(value -> Math.max(0, value - bytes.length));
                    int offset = 0;
                    while (offset < bytes.length && !stopped) {
                        int written = track.write(bytes, offset, bytes.length - offset,
                            AudioTrack.WRITE_BLOCKING);
                        if (written <= 0) throw new IllegalStateException("PCM write failed");
                        offset += written;
                    }
                    frames += offset / 2;
                }
                long drainDeadline = System.currentTimeMillis() + Math.max(3000L, frames / 24L + 3000L);
                while (!stopped && Integer.toUnsignedLong(track.getPlaybackHeadPosition()) < frames
                    && System.currentTimeMillis() < drainDeadline)
                    Thread.sleep(20);
                report(!stopped && Integer.toUnsignedLong(track.getPlaybackHeadPosition()) >= frames);
            } catch (Exception error) {
                report(false);
            } finally {
                playing = false;
                track.release();
                if (pcmPlayer == this) pcmPlayer = null;
            }
        }
    }

    private void startRecognition() {
        if (!recognitionWanted) return;
        boolean onDeviceAvailable = android.os.Build.VERSION.SDK_INT >= 31 &&
            SpeechRecognizer.isOnDeviceRecognitionAvailable(this);
        boolean defaultAvailable = SpeechRecognizer.isRecognitionAvailable(this);
        if (!onDeviceAvailable && !defaultAvailable) {
            callbackVoice("", "设备没有可用的语音识别服务");
            return;
        }
        recognitionOutputOverlap = (tts != null && tts.isSpeaking()) ||
            (pcmPlayer != null && pcmPlayer.playing);
        final boolean usingOnDevice = onDeviceAvailable && !retriedWithDefaultRecognizer;
        if (recognizer == null || recognizerOnDevice != usingOnDevice) {
            if (recognizer != null) recognizer.destroy();
            recognizer = usingOnDevice
                ? SpeechRecognizer.createOnDeviceSpeechRecognizer(this)
                : SpeechRecognizer.createSpeechRecognizer(this);
            recognizerOnDevice = usingOnDevice;
        }
        final SpeechRecognizer activeRecognizer = recognizer;
        final int generation = ++recognitionGeneration;
        recognizerWaiting = true;
        recognitionStartedAtMs = android.os.SystemClock.elapsedRealtime();
        recognizer.setRecognitionListener(new RecognitionListener() {
            @Override public void onReadyForSpeech(Bundle params) {
                if (recognizer == activeRecognizer && generation == recognitionGeneration)
                    callbackSpeechState("listening");
            }
            @Override public void onBeginningOfSpeech() {
                if (recognizer == activeRecognizer && generation == recognitionGeneration) {
                    callbackSpeechState("speaking");
                }
            }
            @Override public void onRmsChanged(float rmsdB) {}
            @Override public void onBufferReceived(byte[] buffer) {}
            @Override public void onEndOfSpeech() {
                if (recognizer == activeRecognizer && generation == recognitionGeneration)
                    callbackSpeechState(holdRecognition && recognitionWanted ? "segment" : "processing");
            }
            @Override public void onError(int error) {
                if (recognizer != activeRecognizer || generation != recognitionGeneration) return;
                recognizerWaiting = false;
                android.util.Log.w("PaperVoice", "Speech recognition error code " + error +
                    " after " + (android.os.SystemClock.elapsedRealtime() - recognitionStartedAtMs) + " ms");
                boolean serviceFailure = error == SpeechRecognizer.ERROR_NETWORK ||
                    error == SpeechRecognizer.ERROR_NETWORK_TIMEOUT ||
                    error == SpeechRecognizer.ERROR_SERVER ||
                    error == SpeechRecognizer.ERROR_SERVER_DISCONNECTED ||
                    error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED ||
                    error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE ||
                    error == SpeechRecognizer.ERROR_CLIENT;
                if (usingOnDevice && defaultAvailable && !retriedWithDefaultRecognizer &&
                    recognitionWanted && serviceFailure) {
                    retriedWithDefaultRecognizer = true;
                    activeRecognizer.destroy();
                    recognizer = null;
                    callbackSpeechState("starting");
                    startRecognition();
                    return;
                }
                if (serviceFailure || error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY) {
                    activeRecognizer.destroy();
                    recognizer = null;
                }
                if (holdRecognition) {
                    if ((error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT)
                        && deferHoldUntilRelease && recognitionWanted) {
                        callbackSpeechState("ended");
                        return;
                    }
                    String message = error == SpeechRecognizer.ERROR_NETWORK ||
                        error == SpeechRecognizer.ERROR_NETWORK_TIMEOUT ||
                        error == SpeechRecognizer.ERROR_SERVER ||
                        error == SpeechRecognizer.ERROR_SERVER_DISCONNECTED
                        ? "系统语音识别服务暂不可用，请稍后重试" :
                        error == SpeechRecognizer.ERROR_AUDIO
                        ? "麦克风暂不可用，请检查其他通话或录音应用" :
                        error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS
                        ? "请检查应用和系统语音服务的麦克风权限" : "";
                    finishHold(message, error == SpeechRecognizer.ERROR_NO_MATCH ||
                        error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT ? "empty" : "");
                    return;
                }
                recognitionWanted = false;
                if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT)
                    callbackVoice("", "", "empty");
                else if (error == SpeechRecognizer.ERROR_NETWORK || error == SpeechRecognizer.ERROR_NETWORK_TIMEOUT ||
                         error == SpeechRecognizer.ERROR_SERVER || error == SpeechRecognizer.ERROR_SERVER_DISCONNECTED)
                    callbackVoice("", "系统语音识别服务暂不可用，请稍后重试");
                else if (error == SpeechRecognizer.ERROR_AUDIO)
                    callbackVoice("", "麦克风暂不可用，请检查其他通话或录音应用");
                else if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS)
                    callbackVoice("", "系统语音服务没有麦克风权限，请检查应用权限设置");
                else if (error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED || error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE)
                    callbackVoice("", "系统语音服务缺少中文识别支持，请安装中文语音包或更换语音服务");
                else callbackVoice("", "系统语音识别连接失败，请重试或使用屏幕按钮");
            }
            @Override public void onResults(Bundle results) {
                if (recognizer != activeRecognizer || generation != recognitionGeneration) return;
                recognizerWaiting = false;
                android.util.Log.i("PaperVoice", "Speech recognition completed after " +
                    (android.os.SystemClock.elapsedRealtime() - recognitionStartedAtMs) + " ms");
                ArrayList<String> words = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                String text = words == null || words.isEmpty() ? "" : words.get(0).trim();
                if (holdRecognition) {
                    if (!text.isEmpty()) {
                        holdWords.add(text);
                        callbackSpeechPartial(android.text.TextUtils.join(" ", holdWords));
                    }
                    if (deferHoldUntilRelease && recognitionWanted) callbackSpeechState("ended");
                    else finishHold("", recognitionOutputOverlap ? "tts_overlap" : "");
                } else {
                    recognitionWanted = false;
                    callbackVoice(text, "", recognitionOutputOverlap ? "tts_overlap" : "");
                }
            }
            @Override public void onPartialResults(Bundle partialResults) {
                if (recognizer != activeRecognizer || generation != recognitionGeneration || !holdRecognition) return;
                ArrayList<String> words = partialResults.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (words != null && !words.isEmpty())
                    callbackSpeechPartial((android.text.TextUtils.join(" ", holdWords) + " " + words.get(0)).trim());
            }
            @Override public void onEvent(int eventType, Bundle params) {}
        });
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN");
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true);
        if (holdRecognition) intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        android.util.Log.i("PaperVoice", "Speech recognition started; mode=" +
            (holdRecognition ? (deferHoldUntilRelease ? "hold" : "reply") : "single") +
            ", provider=" + (usingOnDevice ? "on-device" : "default"));
        recognizer.startListening(intent);
    }

    public final class NativeVoice {
        @JavascriptInterface public void setHoldGestureActive(boolean active) {
            holdGestureActive = active;
        }
        @JavascriptInterface public boolean hasMicrophonePermission() {
            return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
        }
        @JavascriptInterface public void listen() {
            runOnUiThread(() -> {
                recognitionWanted = true;
                retriedWithDefaultRecognizer = false;
                holdRecognition = false;
                deferHoldUntilRelease = false;
                holdWords.clear();
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) startRecognition();
                else {
                    callbackSpeechState("permission");
                    pendingVoice = true;
                    requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 43);
                }
            });
        }
        @JavascriptInterface public void listenHold() {
            runOnUiThread(() -> {
                recognitionWanted = true;
                retriedWithDefaultRecognizer = false;
                holdRecognition = true;
                deferHoldUntilRelease = true;
                holdWords.clear();
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) startRecognition();
                else {
                    callbackSpeechState("permission");
                    pendingVoice = true;
                    requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 43);
                }
            });
        }
        @JavascriptInterface public void listenReply() {
            runOnUiThread(() -> {
                recognitionWanted = true;
                retriedWithDefaultRecognizer = false;
                holdRecognition = true;
                deferHoldUntilRelease = false;
                holdWords.clear();
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) startRecognition();
                else {
                    callbackSpeechState("permission");
                    pendingVoice = true;
                    requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 43);
                }
            });
        }
        @JavascriptInterface public void stopListening() {
            runOnUiThread(() -> {
                pendingVoice = false;
                recognitionWanted = false;
                if (holdRecognition) {
                    if (recognizer != null && recognizerWaiting) {
                        final SpeechRecognizer activeRecognizer = recognizer;
                        recognizer.stopListening();
                        web.postDelayed(() -> {
                            if (holdRecognition && !recognitionWanted && recognizer == activeRecognizer)
                                finishHold("", "timeout");
                        }, 3500);
                    } else finishHold("", "");
                } else if (recognizer != null) recognizer.stopListening();
            });
        }
        @JavascriptInterface public void cancelListening() {
            runOnUiThread(() -> {
                boolean cancelActive = recognizerWaiting;
                pendingVoice = false;
                recognitionWanted = false;
                holdRecognition = false;
                deferHoldUntilRelease = false;
                recognizerWaiting = false;
                recognitionGeneration++;
                holdWords.clear();
                if (recognizer != null && cancelActive) {
                    recognizer.cancel();
                    recognizer.destroy();
                    recognizer = null;
                }
            });
        }
        @JavascriptInterface public void speak(String text, String utteranceId) {
            speakWithRate(text, utteranceId, DEFAULT_SPEECH_RATE);
        }
        @JavascriptInterface public void speakAtRate(String text, String utteranceId, double rate) {
            speakWithRate(text, utteranceId, rate);
        }
        private void speakWithRate(String text, String utteranceId, double requestedRate) {
            final float rate = Double.isFinite(requestedRate)
                ? (float) Math.max(0.5, Math.min(1.2, requestedRate)) : DEFAULT_SPEECH_RATE;
            runOnUiThread(() -> {
                if (pcmPlayer != null) pcmPlayer.stop();
                if (recognitionWanted) recognitionOutputOverlap = true;
                if (tts == null) {
                    callbackSpeechOutputDone(utteranceId);
                    return;
                }
                // Set every utterance, including legacy/system messages after a slow lesson.
                if (tts.setSpeechRate(rate) == TextToSpeech.ERROR
                        || tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId) == TextToSpeech.ERROR) {
                    tts.setSpeechRate(DEFAULT_SPEECH_RATE);
                    callbackSpeechOutputDone(utteranceId);
                }
            });
        }
        @JavascriptInterface public void stop() {
            runOnUiThread(MainActivity.this::stopSystemSpeech);
        }
        @JavascriptInterface public boolean startPcm(String utteranceId) {
            try {
                if (pcmPlayer != null) pcmPlayer.stop();
                stopSystemSpeech();
                pcmPlayer = new PcmPlayer(utteranceId);
                pcmPlayer.start();
                return true;
            } catch (Exception error) {
                android.util.Log.w("PaperVoice", "PCM playback unavailable", error);
                return false;
            }
        }
        @JavascriptInterface public boolean pushPcm(String utteranceId, String encoded) {
            PcmPlayer player = pcmPlayer;
            return player != null && player.utteranceId.equals(utteranceId) && player.push(encoded);
        }
        @JavascriptInterface public void finishPcm(String utteranceId) {
            PcmPlayer player = pcmPlayer;
            if (player != null && player.utteranceId.equals(utteranceId)) player.finish();
        }
        @JavascriptInterface public void stopPcm() {
            PcmPlayer player = pcmPlayer;
            if (player != null) player.stop();
        }
    }

    @Override protected void onPause() {
        super.onPause();
        holdGestureActive = false;
        if (!pendingVoice) recognitionWanted = false;
        boolean cameraPermissionPending = paperCamera != null && paperCamera.permissionPending();
        if (pendingWebPermission == null && !pendingVoice && !cameraPermissionPending)
            web.evaluateJavascript("window.dispatchEvent(new Event('appbackground'))", null);
        if (paperCamera != null) paperCamera.onActivityPause();
        if (recognizer != null) recognizer.cancel();
        stopSystemSpeech();
        if (pcmPlayer != null) pcmPlayer.stop();
        web.onPause();
    }
    @Override protected void onResume() {
        super.onResume();
        if (paperCamera != null) paperCamera.onActivityResume();
        web.onResume();
        web.evaluateJavascript("window.dispatchEvent(new Event('appforeground'))", null);
    }
    @Override protected void onStop() {
        if (paperCamera != null) paperCamera.onActivityStop();
        super.onStop();
    }
    @Override protected void onDestroy() {
        if (paperCamera != null) paperCamera.destroy();
        if (paperVision != null) {
            try { paperVision.close(); }
            catch (Exception error) { android.util.Log.w("PaperVision", "OCR shutdown failed", error); }
        }
        if (recognizer != null) recognizer.destroy();
        if (tts != null) tts.shutdown();
        if (pcmPlayer != null) pcmPlayer.stop();
        web.destroy();
        super.onDestroy();
    }
}

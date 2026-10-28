package com.paperbetween.app;

import android.net.Uri;
import android.webkit.WebResourceResponse;

/** Child-only implementation, loaded without adding UVC dependencies to the parent APK. */
public interface NativeCamera {
    String open();
    String status();
    String close();
    WebResourceResponse frame(Uri uri);
    boolean permissionPending();
    void onCameraPermissionResult(boolean granted);
    void onActivityPause();
    void onActivityResume();
    void onActivityStop();
    void destroy();
}

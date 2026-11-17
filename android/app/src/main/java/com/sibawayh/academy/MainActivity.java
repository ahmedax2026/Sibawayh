package com.sibawayh.academy;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.ProxyInfo;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Native security wrapper; never exposes a JavascriptInterface to third-party frames. */
public class MainActivity extends Activity {
    private WebView web;
    private FrameLayout root;
    private LinearLayout gate;
    private TextView gateText;
    private View fullscreen;
    private WebChromeClient.CustomViewCallback fullscreenCallback;
    private String origin = "";
    private String watchUrl = "";
    private String loadedUrl = "";
    private boolean blocked = false;
    private boolean resumed = false;
    private boolean backgroundBlank = false;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable monitor = new Runnable() {
        @Override public void run() {
            if (resumed && !watchUrl.isEmpty()) enforce();
            handler.postDelayed(this, 600);
        }
    };

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE);
        getWindow().setStatusBarColor(Color.rgb(23,22,21));
        getWindow().setNavigationBarColor(Color.rgb(23,22,21));
        root = new FrameLayout(this);
        if(android.os.Build.VERSION.SDK_INT>=30){
            root.setOnApplyWindowInsetsListener((v,insets)->{
                android.graphics.Insets bars=insets.getInsets(android.view.WindowInsets.Type.systemBars());
                v.setPadding(bars.left,bars.top,bars.right,bars.bottom);return insets;
            });
        }
        setContentView(root);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(-1,-1));
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setSupportMultipleWindows(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setUserAgentString(settings.getUserAgentString()+" SibawayhAndroid/0.1");
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web,true); // Drive embedded preview needs these.
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
                loadedUrl=url;
                if (isOwn(url)) {
                    watchUrl=Uri.parse(url).getPath().startsWith("/watch/")?url:"";
                    if (watchUrl.isEmpty()) {blocked=false;hideGate();}
                    else enforce();
                }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                if (!req.isForMainFrame()) return false;
                String url=req.getUrl().toString();
                if(isOwn(url))return false;
                if(req.hasGesture())openExternal(req.getUrl());
                return true;
            }
            @Override public void onReceivedSslError(WebView view, SslErrorHandler h, SslError error) {
                h.cancel();
                showGate("تعذر التحقق من أمان الاتصال. أوقف أدوات اعتراض الاتصال وتأكد من تاريخ الجهاز.");
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onShowCustomView(View view, CustomViewCallback cb) {
                if(blocked || !blockReason().isEmpty()){cb.onCustomViewHidden();enforce();return;}
                fullscreen=view;fullscreenCallback=cb;
                root.addView(view,new FrameLayout.LayoutParams(-1,-1));
            }
            @Override public void onHideCustomView(){exitFullscreen();}
            @Override public boolean onCreateWindow(WebView view, boolean dialog, boolean userGesture, android.os.Message resultMsg){
                if(!userGesture)return false;
                WebView popup=new WebView(MainActivity.this);
                popup.setWebViewClient(new WebViewClient(){
                    @Override public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){
                        openExternal(r.getUrl());v.destroy();return true;
                    }
                });
                ((WebView.WebViewTransport)resultMsg.obj).setWebView(popup);resultMsg.sendToTarget();return true;
            }
        });
        createGate();
        // Long press is a deliberate configuration entry point, not available to web content.
        gateText.setOnLongClickListener(v->{configure();return true;});
        origin=getPreferences(MODE_PRIVATE).getString("server","");
        if(origin.isEmpty())configure();else web.loadUrl(origin);
        handler.post(monitor);
    }

    private boolean isOwn(String value) {
        try {Uri u=Uri.parse(value),o=Uri.parse(origin);return "https".equals(u.getScheme())&&o.getHost()!=null&&o.getHost().equals(u.getHost())&&o.getPort()==u.getPort();}
        catch(Exception e){return false;}
    }
    private void openExternal(Uri uri){
        // Keep standard provider links functional. YouTube/Drive source links cannot be made app-exclusive.
        if(!"https".equals(uri.getScheme()))return;
        try{startActivity(new Intent(Intent.ACTION_VIEW,uri));}
        catch(Exception e){new AlertDialog.Builder(this).setMessage("لا يوجد تطبيق لفتح هذا الرابط").setPositiveButton("حسنًا",null).show();}
    }
    private void configure(){
        EditText input=new EditText(this);input.setSingleLine(true);input.setText(origin);input.setHint("https://your-server.example");input.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_URI);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("إعداد نسخة سيبويه التجريبية").setMessage("أدخل عنوان خادم سيبويه HTTPS. العنوان يجب أن يكون لخادم تثق به؛ لا تستخدم حسابك على خادم مجهول.").setView(input).setPositiveButton("اتصال",null).setNegativeButton("إغلاق",(d,w)->{if(origin.isEmpty())finish();}).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            String value=input.getText().toString().trim().replaceAll("/+$","");
            Uri uri=Uri.parse(value);
            if(!"https".equals(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null||uri.getQuery()!=null||uri.getFragment()!=null||(uri.getPath()!=null&&!uri.getPath().isEmpty())){input.setError("استخدم عنوان HTTPS فقط بدون مسار");return;}
            CookieManager.getInstance().removeAllCookies(null);
            web.clearCache(true);
            origin=value;watchUrl="";blocked=false;
            getPreferences(MODE_PRIVATE).edit().putString("server",origin).apply();
            hideGate();dialog.dismiss();web.loadUrl(origin);
        }));dialog.show();
    }
    private void createGate(){
        gate=new LinearLayout(this);gate.setOrientation(LinearLayout.VERTICAL);gate.setGravity(Gravity.CENTER);gate.setPadding(40,50,40,50);gate.setBackgroundColor(Color.rgb(250,247,240));
        gateText=new TextView(this);gateText.setTextSize(21);gateText.setGravity(Gravity.CENTER);gateText.setTextColor(Color.rgb(45,36,23));
        gate.addView(gateText,new LinearLayout.LayoutParams(-1,-2));
        Button retry=new Button(this);retry.setText("إعادة التحقق");retry.setOnClickListener(v->{if(blockReason().isEmpty() || watchUrl.isEmpty()){blocked=false;hideGate();web.loadUrl(watchUrl.isEmpty()?origin:watchUrl);}else enforce();});gate.addView(retry);
        Button back=new Button(this);back.setText("العودة للرئيسية");back.setOnClickListener(v->{watchUrl="";blocked=false;hideGate();web.loadUrl(origin);});gate.addView(back);
        gate.setVisibility(View.GONE);root.addView(gate,new FrameLayout.LayoutParams(-1,-1));
    }
    private void showGate(String text){exitFullscreen();gateText.setText(text);gate.setVisibility(View.VISIBLE);gate.bringToFront();}
    private void hideGate(){gate.setVisibility(View.GONE);}
    private boolean hasHeadphones(){
        try{
            AudioManager audio=(AudioManager)getSystemService(AUDIO_SERVICE);
            for(AudioDeviceInfo d:audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)){
                int t=d.getType();
                if(t==AudioDeviceInfo.TYPE_WIRED_HEADPHONES||t==AudioDeviceInfo.TYPE_WIRED_HEADSET||t==AudioDeviceInfo.TYPE_USB_HEADSET||t==AudioDeviceInfo.TYPE_BLUETOOTH_A2DP||t==AudioDeviceInfo.TYPE_BLUETOOTH_SCO||t==AudioDeviceInfo.TYPE_HEARING_AID||t==26)return true;
            }
        }catch(Exception e){return false;}
        return false;
    }
    private String blockReason(){
        try{
            ConnectivityManager cm=(ConnectivityManager)getSystemService(CONNECTIVITY_SERVICE);
            for(Network n:cm.getAllNetworks()){
                NetworkCapabilities caps=cm.getNetworkCapabilities(n);
                if(caps!=null&&caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN))return "vpn";
            }
            ProxyInfo proxy=cm.getDefaultProxy();
            if(proxy!=null)return "proxy";
        }catch(Exception e){return "network_check";}
        if(!hasHeadphones())return "headphones_disconnected";
        return "";
    }
    private void enforce(){
        String reason=blockReason();
        if(!reason.isEmpty()){
            if(!blocked){
                blocked=true;
                if(isOwn(loadedUrl)&&!reason.equals("network_check"))web.evaluateJavascript("window.sibawayhSecurityStop && window.sibawayhSecurityStop('"+reason+"')",null);
                // Removing the document stops even providers with no pause API.
                web.stopLoading();web.loadUrl("about:blank");
            }
            showGate(reason.equals("headphones_disconnected")?"وصّل سماعة سلك أو بلوتوث علشان تشاهد الدرس.":reason.equals("network_check")?"تعذر فحص الاتصال. أعد المحاولة.":"أوقف VPN أو البروكسي وأدوات فحص الاتصال، ثم أعد المحاولة.");
        }else if(blocked||backgroundBlank){
            blocked=false;backgroundBlank=false;hideGate();web.loadUrl(watchUrl);
        }
    }
    private void exitFullscreen(){
        if(fullscreen!=null){root.removeView(fullscreen);fullscreen=null;WebChromeClient.CustomViewCallback cb=fullscreenCallback;fullscreenCallback=null;if(cb!=null)cb.onCustomViewHidden();}
    }
    @Override protected void onResume(){super.onResume();resumed=true;if(web!=null){web.onResume();web.resumeTimers();if(!watchUrl.isEmpty())enforce();}}
    @Override protected void onPause(){
        resumed=false;
        if(web!=null){if(!watchUrl.isEmpty()){exitFullscreen();web.evaluateJavascript("window.sibawayhSecurityPause && window.sibawayhSecurityPause()",null);backgroundBlank=true;web.loadUrl("about:blank");}web.onPause();web.pauseTimers();}
        super.onPause();
    }
    @Override public void onBackPressed(){if(fullscreen!=null){exitFullscreen();return;}if(gate.getVisibility()==View.VISIBLE){watchUrl="";blocked=false;hideGate();web.loadUrl(origin);return;}if(web.canGoBack())web.goBack();else super.onBackPressed();}
    @Override protected void onDestroy(){handler.removeCallbacks(monitor);if(web!=null){web.stopLoading();web.destroy();}super.onDestroy();}
}

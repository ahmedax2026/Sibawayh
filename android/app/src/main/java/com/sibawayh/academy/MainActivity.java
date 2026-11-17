package com.sibawayh.academy;
import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.view.WindowManager;
public class MainActivity extends Activity {
 private WebView web;
 @Override public void onCreate(Bundle state){super.onCreate(state);getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE,WindowManager.LayoutParams.FLAG_SECURE);web=new WebView(this);setContentView(web);web.getSettings().setJavaScriptEnabled(true);web.getSettings().setDomStorageEnabled(true);web.getSettings().setAllowContentAccess(false);web.getSettings().setAllowFileAccess(false);web.setWebViewClient(new WebViewClient());web.loadUrl("file:///android_asset/demo.html");}
 @Override public void onBackPressed(){if(web.canGoBack())web.goBack();else super.onBackPressed();}
 @Override protected void onDestroy(){web.destroy();super.onDestroy();}
}
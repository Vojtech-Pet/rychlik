package test.sink;

import android.app.Activity;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.util.Log;
import android.widget.TextView;
import java.io.InputStream;
import java.security.MessageDigest;

/** Test-only share target: proves a generic ACTION_SEND app can read the content:// URI FriendSend hands over. */
public class SinkActivity extends Activity {
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        Intent i = getIntent();
        Uri uri = i.getParcelableExtra(Intent.EXTRA_STREAM);
        String report;
        CharSequence sharedText = i.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (uri == null && sharedText != null) {
            // text share: report the exact received string (bracketed so trailing whitespace is visible)
            report = "SINK_TEXT text=[" + sharedText + "] length=" + sharedText.length() + " type=" + i.getType() + " component=" + i.getComponent();
            Log.i("ShareSink", report);
            TextView tv = new TextView(this);
            tv.setText(report);
            tv.setTextSize(12);
            setContentView(tv);
            return;
        }
        try {
            String name = "?"; long size = -1;
            try (Cursor c = getContentResolver().query(uri, null, null, null, null)) {
                if (c != null && c.moveToFirst()) {
                    int n = c.getColumnIndex(OpenableColumns.DISPLAY_NAME); int s = c.getColumnIndex(OpenableColumns.SIZE);
                    if (n >= 0) name = c.getString(n);
                    if (s >= 0) size = c.getLong(s);
                }
            }
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            long total = 0;
            try (InputStream in = getContentResolver().openInputStream(uri)) {
                byte[] buf = new byte[65536]; int r;
                while ((r = in.read(buf)) > 0) { md.update(buf, 0, r); total += r; }
            }
            StringBuilder hex = new StringBuilder();
            for (byte x : md.digest()) hex.append(String.format("%02x", x));
            report = "SINK_OK scheme=" + uri.getScheme() + " display_name=" + name + " size=" + size + " read=" + total + " sha256=" + hex + " type=" + i.getType() + " component=" + i.getComponent();
        } catch (Exception e) {
            report = "SINK_FAIL " + e;
        }
        Log.i("ShareSink", report);
        TextView t = new TextView(this);
        t.setText(report);
        t.setTextSize(12);
        setContentView(t);
    }
}

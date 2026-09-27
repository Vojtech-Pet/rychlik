package app.friendsend.friendsend

import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

/**
 * Prompt A14 §51/§131-135: a deliberately tiny Flutter<->Kotlin bridge.
 * One method (`shareFile`), one bounded result string. Kotlin owns all
 * Android-specific FileProvider/Intent work; Dart never sees a raw
 * Android exception (§134).
 */
class MainActivity : FlutterActivity() {
    private val channelName = "app.friendsend/share"

    // Prompt A14 §54: must match the authority declared in AndroidManifest.xml
    // and the <cache-path> declared in res/xml/file_paths.xml.
    private val fileProviderAuthority = "app.friendsend.friendsend.fileprovider"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName).setMethodCallHandler { call, result ->
            if (call.method == "shareFile") {
                val path = call.argument<String>("path")
                val displayName = call.argument<String>("displayName") ?: "shared_file"
                val mimeType = call.argument<String>("mimeType") ?: "application/octet-stream"
                result.success(handleShareFile(path, displayName, mimeType))
            } else {
                result.notImplemented()
            }
        }
    }

    /**
     * Prompt A14 §132/§133 (defense in depth): even though Dart is only
     * ever supposed to call this for a verified FriendSend temp file, this
     * method independently refuses to build a content:// grant for any
     * path outside its own private FriendSend cache subtree -- a buggy or
     * malicious Dart call can never turn this into arbitrary-file sharing.
     */
    private fun handleShareFile(path: String?, displayName: String, mimeType: String): String {
        if (path.isNullOrEmpty()) {
            return "INVALID_TEMP_FILE"
        }
        val allowedRoot = File(cacheDir, "friendsend").canonicalFile
        val requested = File(path).canonicalFile
        if (!requested.path.startsWith(allowedRoot.path + File.separator) && requested.path != allowedRoot.path) {
            return "INVALID_TEMP_FILE"
        }
        if (!requested.exists() || !requested.isFile) {
            return "INVALID_TEMP_FILE"
        }

        return try {
            // Prompt A14 §53: content:// only, never file://.
            val uri: Uri = FileProvider.getUriForFile(this, fileProviderAuthority, requested)
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = mimeType
                putExtra(Intent.EXTRA_STREAM, uri)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            // Prompt A14 §56/§57: the standard OS chooser -- no hard-coded
            // target package, no accessibility auto-click, no private API.
            val chooser = Intent.createChooser(intent, displayName)
            if (chooser.resolveActivity(packageManager) == null) {
                "NO_SHARE_TARGET"
            } else {
                startActivity(chooser)
                "SHARE_SHEET_OPENED"
            }
        } catch (_: Exception) {
            "PLATFORM_ERROR"
        }
    }
}

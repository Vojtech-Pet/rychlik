package app.friendsend.friendsend

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
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
 *
 * Prompt A15 §54/§112-114 adds a second, equally narrow bridge for mDNS/
 * DNS-SD advertisement via the native `NsdManager` -- no custom
 * multicast protocol, no generic networking API surface.
 */
class MainActivity : FlutterActivity() {
    private val shareChannelName = "app.friendsend/share"
    private val mdnsChannelName = "app.friendsend/mdns"

    // Prompt A14 §54: must match the authority declared in AndroidManifest.xml
    // and the <cache-path> declared in res/xml/file_paths.xml.
    private val fileProviderAuthority = "app.friendsend.friendsend.fileprovider"

    private val serviceType = "_friendsend._tcp"
    private var nsdManager: NsdManager? = null
    private var registrationListener: NsdManager.RegistrationListener? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, shareChannelName).setMethodCallHandler { call, result ->
            if (call.method == "shareFile") {
                val path = call.argument<String>("path")
                val displayName = call.argument<String>("displayName") ?: "shared_file"
                val mimeType = call.argument<String>("mimeType") ?: "application/octet-stream"
                result.success(handleShareFile(path, displayName, mimeType))
            } else {
                result.notImplemented()
            }
        }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, mdnsChannelName).setMethodCallHandler { call, result ->
            when (call.method) {
                "registerFriendSendService" -> {
                    val port = call.argument<Int>("port") ?: 0
                    val deviceId = call.argument<String>("deviceId") ?: ""
                    val protocolVersion = call.argument<Int>("protocolVersion") ?: 1
                    val securityProfile = call.argument<String>("securityProfile") ?: ""
                    result.success(handleRegisterMdns(port, deviceId, protocolVersion, securityProfile))
                }
                "unregisterFriendSendService" -> {
                    handleUnregisterMdns()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }

    /**
     * Advertises ONLY non-secret metadata (§56 of the A15 prompt):
     * device_id, protocol_version, security_profile. Never a pairing
     * secret, desktop key, or auth signature.
     */
    private fun handleRegisterMdns(port: Int, deviceId: String, protocolVersion: Int, securityProfile: String): String {
        if (port <= 0 || deviceId.isEmpty()) {
            return "FAILED"
        }
        return try {
            val manager = (nsdManager ?: (getSystemService(Context.NSD_SERVICE) as NsdManager)).also { nsdManager = it }
            val serviceInfo = NsdServiceInfo().apply {
                serviceName = deviceId
                serviceType = this@MainActivity.serviceType
                setPort(port)
                setAttribute("device_id", deviceId)
                setAttribute("protocol_version", protocolVersion.toString())
                setAttribute("security_profile", securityProfile)
            }
            val listener = object : NsdManager.RegistrationListener {
                override fun onRegistrationFailed(info: NsdServiceInfo, errorCode: Int) {}
                override fun onUnregistrationFailed(info: NsdServiceInfo, errorCode: Int) {}
                override fun onServiceRegistered(info: NsdServiceInfo) {}
                override fun onServiceUnregistered(info: NsdServiceInfo) {}
            }
            manager.registerService(serviceInfo, NsdManager.PROTOCOL_DNS_SD, listener)
            registrationListener = listener
            "REGISTERED"
        } catch (_: Exception) {
            "FAILED"
        }
    }

    private fun handleUnregisterMdns() {
        val manager = nsdManager
        val listener = registrationListener
        if (manager != null && listener != null) {
            try {
                manager.unregisterService(listener)
            } catch (_: Exception) {
                // Best-effort cleanup only.
            }
        }
        registrationListener = null
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
            // Prompt A17-E1: the physical temp file keeps its safe,
            // UUID-derived name (TempCache never trusts a declared
            // filename as a disk path) -- only the FileProvider's
            // *advertised* OpenableColumns.DISPLAY_NAME changes, via the
            // 4-arg getUriForFile overload (AndroidX Core 1.5.0+; this
            // project resolves 1.13.1). The network-declared name can
            // therefore never influence where anything is written, only
            // what a recipient sees it called.
            val safeDisplayName = sanitizeShareDisplayName(rawDisplayName = displayName, mimeType = mimeType)
            // Prompt A14 §53: content:// only, never file://.
            val uri: Uri = FileProvider.getUriForFile(this, fileProviderAuthority, requested, safeDisplayName)
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = mimeType
                putExtra(Intent.EXTRA_STREAM, uri)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            // Prompt A14 §56/§57: the standard OS chooser -- no hard-coded
            // target package, no accessibility auto-click, no private API.
            // Prompt A17-E1: a plain, generic chooser title -- the actual
            // filename is now the URI's own DISPLAY_NAME, not this string.
            val chooser = Intent.createChooser(intent, "Share with…")
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

/**
 * Prompt A17-E1: turns a network-declared display name into a safe
 * basename for FileProvider's DISPLAY_NAME column only -- never used as a
 * filesystem path. Strips any directory component (so "../../evil.mp4"
 * becomes just "evil.mp4", never escaping anywhere since it is never
 * resolved against a directory at all), strips control characters and
 * NUL, and falls back to a generic name when nothing sane remains.
 *
 * A top-level, Android-framework-free function (no `Context`/`Activity`
 * dependency) so it is a plain JVM-testable unit, not something that
 * needs a real Android runtime/instrumented test to exercise.
 */
fun sanitizeShareDisplayName(rawDisplayName: String, mimeType: String): String {
    val basename = rawDisplayName.substringAfterLast('/').substringAfterLast('\\')
    val cleaned = basename.filter { it.code >= 0x20 && it.code != 0x7f }.trim()
    if (cleaned.isEmpty() || cleaned == "." || cleaned == "..") {
        val extension = when {
            mimeType.startsWith("video/") -> ".mp4"
            mimeType.startsWith("audio/") -> ".m4a"
            else -> ""
        }
        return "shared_file$extension"
    }
    return cleaned
}

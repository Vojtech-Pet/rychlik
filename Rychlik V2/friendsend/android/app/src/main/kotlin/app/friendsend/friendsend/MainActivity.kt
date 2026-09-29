package app.friendsend.friendsend

import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.Context
import android.content.ComponentName
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
    private val incomingChannelName = "app.friendsend/incoming"
    private val mediaChannelName = "app.friendsend/media"
    private val videoDownloader by lazy { VideoDownloader(this) }

    // Prompt A14 §54: must match the authority declared in AndroidManifest.xml
    // and the <cache-path> declared in res/xml/file_paths.xml.
    private val fileProviderAuthority = "app.friendsend.friendsend.fileprovider"

    private val serviceType = "_friendsend._tcp"
    private var nsdManager: NsdManager? = null
    private var registrationListener: NsdManager.RegistrationListener? = null
    private val shareTargetResolver by lazy { ShareTargetResolver(this) }

    // Text another app shared to FriendSend (ACTION_SEND text/plain). Kept until Dart takes it, so a cold start
    // (Dart not listening yet) never loses it; delivered announced through the incoming channel when Dart is running (Dart pulls it once).
    private var pendingShareText: String? = null
    private var incomingChannel: MethodChannel? = null

    override fun onCreate(savedInstanceState: android.os.Bundle?) {
        super.onCreate(savedInstanceState)
        captureIncomingShare(intent)
        publishShareShortcut()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (captureIncomingShare(intent)) incomingChannel?.invokeMethod("incomingAvailable", null) // Dart then pulls it once
    }

    /** A long-lived sharing shortcut so the system Sharesheet can offer FriendSend in its top (direct share) row. */
    private fun publishShareShortcut() {
        try {
            val shortcut = androidx.core.content.pm.ShortcutInfoCompat.Builder(this, "friendsend_text_share")
                .setShortLabel("FriendSend")
                .setLongLived(true)
                .setIcon(androidx.core.graphics.drawable.IconCompat.createWithResource(this, R.mipmap.ic_launcher))
                .setIntent(Intent(this, MainActivity::class.java).setAction(Intent.ACTION_VIEW))
                .setCategories(setOf("app.friendsend.category.TEXT_SHARE"))
                .build()
            androidx.core.content.pm.ShortcutManagerCompat.pushDynamicShortcut(this, shortcut)
        } catch (_: Exception) {
            // Best effort: FriendSend is still listed among all apps without it.
        }
    }

    private fun captureIncomingShare(source: Intent?): Boolean {
        if (source == null) return false
        val text = IncomingShare.extractText(source.action, source.type, source.getCharSequenceExtra(Intent.EXTRA_TEXT)) ?: return false
        pendingShareText = text
        source.action = null // consumed: a recreated activity must not process the same share again
        return true
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, shareChannelName).setMethodCallHandler { call, result ->
            when (call.method) {
                "shareFile" -> {
                    val path = call.argument<String>("path")
                    val displayName = call.argument<String>("displayName") ?: "shared_file"
                    val mimeType = call.argument<String>("mimeType") ?: "application/octet-stream"
                    result.success(handleShareFile(path, displayName, mimeType))
                }
                "listShareTargets" -> {
                    val mimeType = call.argument<String>("mimeType") ?: "application/octet-stream"
                    Thread {
                        val targets = try {
                            shareTargetResolver.resolve(mimeType)
                        } catch (_: Exception) {
                            emptyList()
                        }
                        runOnUiThread { result.success(targets) }
                    }.start()
                }
                "shareTextToTarget" -> result.success(handleShareTextToTarget(call.argument<String>("text"), call.argument<String>("targetId")))
                "shareText" -> result.success(handleShareText(call.argument<String>("text")))
                "shareToTarget" -> {
                    val path = call.argument<String>("path")
                    val displayName = call.argument<String>("displayName") ?: "shared_file"
                    val mimeType = call.argument<String>("mimeType") ?: "application/octet-stream"
                    result.success(handleShareToTarget(path, displayName, mimeType, call.argument<String>("targetId")))
                }
                else -> result.notImplemented()
            }
        }
        incomingChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, incomingChannelName).also { channel ->
            channel.setMethodCallHandler { call, result ->
                when (call.method) {
                    "takeIncomingText" -> {
                        result.success(pendingShareText)
                        pendingShareText = null
                    }
                    else -> result.notImplemented()
                }
            }
        }
        val mediaChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, mediaChannelName)
        mediaChannel.setMethodCallHandler { call, result ->
            when (call.method) {
                "downloadVideo" -> {
                    val url = call.argument<String>("url").orEmpty()
                    if (!(url.startsWith("http://") || url.startsWith("https://"))) {
                        result.success(mapOf("error" to "The address is not a web link"))
                    } else {
                        Thread {
                            val outcome: Map<String, Any?> = try {
                                val d = videoDownloader.download(url) { done, total ->
                                    runOnUiThread { mediaChannel.invokeMethod("videoProgress", mapOf("done" to done, "total" to total)) }
                                }
                                mapOf("path" to d.path, "displayName" to d.displayName, "mimeType" to d.mimeType, "size" to d.size)
                            } catch (f: VideoDownloader.Failure) {
                                if (f.cancelled) mapOf("cancelled" to true) else mapOf("error" to (f.message ?: "The video could not be downloaded"))
                            } catch (e: Exception) {
                                android.util.Log.w("FriendSendVideo", "unexpected: " + e)
                                mapOf("error" to "The video could not be downloaded")
                            }
                            runOnUiThread { result.success(outcome) }
                        }.start()
                    }
                }
                "saveVideo" -> result.success(saveVideoToGallery(call.argument<String>("path"), call.argument<String>("displayName"), call.argument<String>("mimeType")))
                "cancelVideoDownload" -> {
                    videoDownloader.cancel()
                    result.success(null)
                }
                else -> result.notImplemented()
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
        val prepared = prepareShareUri(path, displayName, mimeType)
        val uri = prepared.first ?: return prepared.second
        return try {
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

    /**
     * Targeted ACTION_SEND to one component chosen from the picker. Same private-cache guard and DISPLAY_NAME
     * handling as [handleShareFile]; the component is re-checked against the system right before launching so a
     * vanished app produces TARGET_UNAVAILABLE instead of a crash. Returns TARGET_OPENED only when the system
     * accepted the launch -- never a claim that anyone received the file.
     */
    private fun handleShareToTarget(path: String?, displayName: String, mimeType: String, targetId: String?): String {
        val component = parseComponentId(targetId) ?: return "TARGET_UNAVAILABLE"
        val prepared = prepareShareUri(path, displayName, mimeType)
        val uri = prepared.first ?: return prepared.second
        if (!shareTargetResolver.isStillAvailable(mimeType, component.first, component.second)) {
            return "TARGET_UNAVAILABLE"
        }
        return try {
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = mimeType
                putExtra(Intent.EXTRA_STREAM, uri)
                clipData = ClipData.newRawUri("", uri)
                setComponent(shareTargetResolver.component(component.first, component.second))
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivity(intent)
            "TARGET_OPENED"
        } catch (_: ActivityNotFoundException) {
            "TARGET_UNAVAILABLE"
        } catch (_: Exception) {
            "PLATFORM_ERROR"
        }
    }

    /**
     * Copies a downloaded video (only from FriendSend's private video cache) into the shared Movies/FriendSend folder
     * through MediaStore, so it shows up in the Gallery. Android 10+ needs no storage permission for this.
     */
    private fun saveVideoToGallery(path: String?, displayName: String?, mimeType: String?): String {
        if (android.os.Build.VERSION.SDK_INT < 29) return "UNSUPPORTED"
        if (path.isNullOrEmpty()) return "FAILED"
        val source = try { File(path).canonicalFile } catch (_: Exception) { return "FAILED" }
        val allowedRoot = videoDownloader.directory.canonicalFile
        if (!source.path.startsWith(allowedRoot.path + File.separator) || !source.isFile) return "FAILED"
        val name = sanitizeShareDisplayName(displayName ?: source.name, mimeType ?: "video/mp4")
        val resolver = contentResolver
        val values = android.content.ContentValues().apply {
            put(android.provider.MediaStore.Video.Media.DISPLAY_NAME, name)
            put(android.provider.MediaStore.Video.Media.MIME_TYPE, mimeType ?: "video/mp4")
            put(android.provider.MediaStore.Video.Media.RELATIVE_PATH, "Movies/FriendSend")
            put(android.provider.MediaStore.Video.Media.IS_PENDING, 1)
        }
        val uri = resolver.insert(android.provider.MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values) ?: return "FAILED"
        return try {
            resolver.openOutputStream(uri)!!.use { out -> source.inputStream().use { it.copyTo(out) } }
            resolver.update(uri, android.content.ContentValues().apply { put(android.provider.MediaStore.Video.Media.IS_PENDING, 0) }, null, null)
            "SAVED"
        } catch (_: Exception) {
            try { resolver.delete(uri, null, null) } catch (_: Exception) {}
            "FAILED"
        }
    }

    /** Targeted ACTION_SEND text/plain: the text goes to the chosen app byte for byte; nothing else is added. */
    private fun handleShareTextToTarget(text: String?, targetId: String?): String {
        val component = parseComponentId(targetId) ?: return "TARGET_UNAVAILABLE"
        if (text.isNullOrEmpty()) return "PLATFORM_ERROR"
        if (!shareTargetResolver.isStillAvailable("text/plain", component.first, component.second)) return "TARGET_UNAVAILABLE"
        return try {
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_TEXT, text)
                setComponent(shareTargetResolver.component(component.first, component.second))
            }
            startActivity(intent)
            "TARGET_OPENED"
        } catch (_: ActivityNotFoundException) {
            "TARGET_UNAVAILABLE"
        } catch (_: Exception) {
            "PLATFORM_ERROR"
        }
    }

    /** The Android Sharesheet for the same text, with FriendSend itself excluded so it cannot loop back here. */
    private fun handleShareText(text: String?): String {
        if (text.isNullOrEmpty()) return "PLATFORM_ERROR"
        return try {
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_TEXT, text)
            }
            val chooser = Intent.createChooser(intent, "Share with…").apply {
                putExtra(Intent.EXTRA_EXCLUDE_COMPONENTS, arrayOf(ComponentName(this@MainActivity, MainActivity::class.java)))
            }
            if (chooser.resolveActivity(packageManager) == null) "NO_SHARE_TARGET" else {
                startActivity(chooser)
                "SHARE_SHEET_OPENED"
            }
        } catch (_: Exception) {
            "PLATFORM_ERROR"
        }
    }

    /** Returns (uri, "") on success or (null, resultCode). */
    private fun prepareShareUri(path: String?, displayName: String, mimeType: String): Pair<Uri?, String> {
        if (path.isNullOrEmpty()) {
            return null to "INVALID_TEMP_FILE"
        }
        val allowedRoot = File(cacheDir, "friendsend").canonicalFile
        val requested = File(path).canonicalFile
        if (!requested.path.startsWith(allowedRoot.path + File.separator) && requested.path != allowedRoot.path) {
            return null to "INVALID_TEMP_FILE"
        }
        if (!requested.exists() || !requested.isFile) {
            return null to "INVALID_TEMP_FILE"
        }
        return try {
            // Prompt A17-E1: the physical temp file keeps its safe,
            // UUID-derived name (TempCache never trusts a declared
            // filename as a disk path) -- only the FileProvider's
            // *advertised* OpenableColumns.DISPLAY_NAME changes, via the
            // 4-arg getUriForFile overload. The network-declared name can
            // therefore never influence where anything is written, only
            // what a recipient sees it called.
            val safeDisplayName = sanitizeShareDisplayName(rawDisplayName = displayName, mimeType = mimeType)
            // Prompt A14 §53: content:// only, never file://.
            FileProvider.getUriForFile(this, fileProviderAuthority, requested, safeDisplayName) to ""
        } catch (_: Exception) {
            null to "PLATFORM_ERROR"
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

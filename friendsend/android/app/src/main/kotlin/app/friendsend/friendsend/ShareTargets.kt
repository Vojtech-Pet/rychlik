package app.friendsend.friendsend

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ResolveInfo
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.Drawable
import android.util.LruCache
import java.io.ByteArrayOutputStream

/** One activity Android reports as able to handle ACTION_SEND for the file's MIME type. */
data class RawShareTarget(val packageName: String, val className: String, val label: String, val social: Boolean = false)

fun componentId(packageName: String, className: String): String = "$packageName/$className"

/** Strict parse of the opaque id Dart hands back; anything malformed is rejected before it reaches an Intent. */
fun parseComponentId(id: String?): Pair<String, String>? {
    if (id.isNullOrEmpty()) return null
    val parts = id.split('/')
    if (parts.size != 2 || parts[0].isEmpty() || parts[1].isEmpty()) return null
    if (parts.any { part -> part.any { it.isWhitespace() || it.code < 0x20 } }) return null
    return parts[0] to parts[1]
}

/**
 * Pure, Android-free selection policy (JVM-testable): what the picker may show.
 * No hard-coded provider names anywhere -- whatever the system resolves is offered, minus FriendSend itself.
 */
object ShareTargetPolicy {
    const val MAX_TARGETS = 24

    fun select(raw: List<RawShareTarget>, ownPackage: String, max: Int = MAX_TARGETS): List<RawShareTarget> {
        val seenComponents = HashSet<String>()
        val seenPackageLabel = HashSet<String>()
        val out = ArrayList<RawShareTarget>()
        val ordered = raw.sortedWith(
            // Apps Android itself categorises as social/messaging come first (the system's own metadata, not a name list).
            compareBy<RawShareTarget>({ !it.social }, { it.label.ifBlank { it.packageName }.lowercase() }, { it.packageName }, { it.className }),
        )
        for (t in ordered) {
            if (t.packageName == ownPackage || t.packageName.isEmpty() || t.className.isEmpty()) continue
            if (!seenComponents.add(componentId(t.packageName, t.className))) continue
            val label = t.label.ifBlank { t.packageName }
            if (!seenPackageLabel.add(t.packageName + "\u0000" + label)) continue
            out.add(t.copy(label = label))
            if (out.size >= max) break
        }
        return out
    }
}

/**
 * Asks the system (never a fixed list) which installed apps can take this file. Needs only the narrow
 * `<queries>` intent declaration in the manifest -- no QUERY_ALL_PACKAGES, no contacts, no private API. It
 * cannot and does not see people or chats inside those apps.
 */
class ShareTargetResolver(private val context: Context) {
    private val iconCache = object : LruCache<String, ByteArray>(24) {}

    private fun sendIntent(mimeType: String): Intent = Intent(Intent.ACTION_SEND).apply { type = mimeType }

    private fun query(mimeType: String): List<ResolveInfo> =
        context.packageManager.queryIntentActivities(sendIntent(mimeType), PackageManager.MATCH_DEFAULT_ONLY)

    fun resolve(mimeType: String): List<Map<String, Any?>> {
        val pm = context.packageManager
        val raw = query(mimeType).mapNotNull { info ->
            val a = info.activityInfo ?: return@mapNotNull null
            if (!a.exported) return@mapNotNull null
            RawShareTarget(a.packageName, a.name, info.loadLabel(pm)?.toString().orEmpty(), isSocial(a.applicationInfo))
        }
        return ShareTargetPolicy.select(raw, context.packageName).map { t ->
            val id = componentId(t.packageName, t.className)
            mapOf("id" to id, "label" to t.label, "icon" to iconFor(id, t))
        }
    }

    private fun isSocial(app: android.content.pm.ApplicationInfo?): Boolean =
        app != null && android.os.Build.VERSION.SDK_INT >= 26 && app.category == android.content.pm.ApplicationInfo.CATEGORY_SOCIAL

    /** True only if this exact component still resolves for the MIME type (an app may have been uninstalled). */
    fun isStillAvailable(mimeType: String, packageName: String, className: String): Boolean =
        query(mimeType).any { it.activityInfo?.packageName == packageName && it.activityInfo?.name == className && it.activityInfo?.exported == true }

    fun component(packageName: String, className: String) = ComponentName(packageName, className)

    private fun iconFor(id: String, t: RawShareTarget): ByteArray? {
        iconCache.get(id)?.let { return it }
        return try {
            val drawable = context.packageManager.getActivityIcon(ComponentName(t.packageName, t.className))
            val bytes = toPng(drawable, ICON_PX)
            iconCache.put(id, bytes)
            bytes
        } catch (_: Exception) {
            null // a neutral placeholder tile is shown for this app; the label still identifies it
        }
    }

    private fun toPng(drawable: Drawable, px: Int): ByteArray {
        val bitmap = Bitmap.createBitmap(px, px, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        drawable.setBounds(0, 0, px, px)
        drawable.draw(canvas)
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
        bitmap.recycle()
        return out.toByteArray()
    }

    companion object {
        private const val ICON_PX = 132
    }
}

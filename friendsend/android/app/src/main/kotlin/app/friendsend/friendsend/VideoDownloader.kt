package app.friendsend.friendsend

import android.content.Context
import com.chaquo.python.PyException
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import org.json.JSONObject

/** Downloads the video behind a shared link with yt-dlp (Python via Chaquopy) into FriendSend's private cache. */
class VideoDownloader(private val context: Context) {
    private val cancelFlag = AtomicBoolean(false)

    /** Only ever inside `cacheDir/friendsend`, the one root the share code is willing to hand out. */
    val directory: File get() = File(context.cacheDir, "friendsend/video")

    fun cancel() = cancelFlag.set(true)

    /** Removes downloads older than [maxAgeMs] (a target app has long finished reading them by then). */
    fun purgeOld(maxAgeMs: Long = 60 * 60 * 1000L) {
        val cutoff = System.currentTimeMillis() - maxAgeMs
        directory.listFiles()?.forEach { if (it.isFile && it.lastModified() < cutoff) it.delete() }
    }

    class Failure(val cancelled: Boolean, message: String) : Exception(message)

    class Downloaded(val path: String, val displayName: String, val mimeType: String, val size: Long)

    /** Called from Python. */
    inner class Control(private val sink: (Long, Long) -> Unit) {
        fun isCancelled(): Boolean = cancelFlag.get()
        fun onProgress(done: Long, total: Long) = sink.invoke(done, total)
    }

    /** Blocking; call off the main thread. */
    fun download(url: String, onProgress: (Long, Long) -> Unit): Downloaded {
        if (!Python.isStarted()) Python.start(AndroidPlatform(context))
        purgeOld()
        cancelFlag.set(false)
        try {
            val json = Python.getInstance().getModule("friendsend_media")
                .callAttr("download", url, directory.absolutePath, Control(onProgress)).toString()
            val o = JSONObject(json)
            return Downloaded(o.getString("path"), o.getString("display_name"), o.getString("mime"), o.getLong("size"))
        } catch (e: PyException) {
            android.util.Log.w("FriendSendVideo", "download failed: " + e.message)
            val cancelled = e.message?.contains("Cancelled") == true
            throw Failure(cancelled, if (cancelled) "cancelled" else (e.message?.substringAfter(": ")?.take(300) ?: "The video could not be downloaded"))
        }
    }
}

package app.friendsend.friendsend

/** Android-free (JVM-testable) rule for what FriendSend accepts from another app's Share button. */
object IncomingShare {
    const val ACTION_SEND = "android.intent.action.SEND"
    const val MAX_TEXT_CHARS = 100_000

    /**
     * The shared text exactly as the source app sent it (never trimmed or rewritten), or null when the intent is
     * not a plain-text share, has no text, or is implausibly large.
     */
    fun extractText(action: String?, mimeType: String?, text: CharSequence?): String? {
        if (action != ACTION_SEND) return null
        if (mimeType == null || !(mimeType == "text/plain" || mimeType.startsWith("text/plain;"))) return null
        val value = text?.toString() ?: return null
        if (value.isBlank() || value.length > MAX_TEXT_CHARS) return null
        return value
    }
}

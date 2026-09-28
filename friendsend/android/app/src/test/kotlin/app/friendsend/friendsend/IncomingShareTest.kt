package app.friendsend.friendsend

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class IncomingShareTest {
    private val url = "https://www.facebook.com/share/v/1AbCdEf/?mibextid=abc&x=1#frag"

    @Test
    fun plainTextShareIsReturnedExactly() {
        assertEquals(url, IncomingShare.extractText("android.intent.action.SEND", "text/plain", url))
    }

    @Test
    fun textIsNeverTrimmedOrRewritten() {
        val text = "  Look at this\nhttps://example.com/a?b=c  \n"
        assertEquals(text, IncomingShare.extractText("android.intent.action.SEND", "text/plain", text))
    }

    @Test
    fun mimeWithParametersIsAccepted() {
        assertEquals(url, IncomingShare.extractText("android.intent.action.SEND", "text/plain; charset=utf-8", url))
    }

    @Test
    fun otherActionsAndMimeTypesAreIgnored() {
        assertNull(IncomingShare.extractText("android.intent.action.VIEW", "text/plain", url))
        assertNull(IncomingShare.extractText("android.intent.action.SEND_MULTIPLE", "text/plain", url))
        assertNull(IncomingShare.extractText("android.intent.action.SEND", "image/png", url))
        assertNull(IncomingShare.extractText("android.intent.action.SEND", "text/html", url))
        assertNull(IncomingShare.extractText("android.intent.action.SEND", null, url))
        assertNull(IncomingShare.extractText(null, "text/plain", url))
    }

    @Test
    fun missingBlankOrHugeTextIsIgnored() {
        assertNull(IncomingShare.extractText("android.intent.action.SEND", "text/plain", null))
        assertNull(IncomingShare.extractText("android.intent.action.SEND", "text/plain", "   \n"))
        assertNull(IncomingShare.extractText("android.intent.action.SEND", "text/plain", "x".repeat(IncomingShare.MAX_TEXT_CHARS + 1)))
        assertEquals(IncomingShare.MAX_TEXT_CHARS, IncomingShare.extractText("android.intent.action.SEND", "text/plain", "x".repeat(IncomingShare.MAX_TEXT_CHARS))!!.length)
    }
}

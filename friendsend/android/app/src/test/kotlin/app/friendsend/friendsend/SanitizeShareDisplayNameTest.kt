package app.friendsend.friendsend

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Prompt A17-E1: regression coverage for the bug a real Android emulator
 * Sharesheet run exposed -- the shared file's DISPLAY_NAME was showing
 * the internal `incoming-<uuid>.bin` TempCache filename instead of the
 * real received name. sanitizeShareDisplayName() is what now feeds
 * FileProvider's 4-arg getUriForFile() overload; it must never let a
 * network-declared name influence a filesystem path (that job stays with
 * TempCache's own UUID-only naming), only what is displayed.
 */
class SanitizeShareDisplayNameTest {
    @Test
    fun `ordinary filename passes through unchanged`() {
        assertEquals("a17e1_passthrough.mp4", sanitizeShareDisplayName("a17e1_passthrough.mp4", "video/mp4"))
    }

    @Test
    fun `path traversal is reduced to a safe basename, never a path`() {
        assertEquals("evil.mp4", sanitizeShareDisplayName("../../evil.mp4", "video/mp4"))
        assertEquals("evil.mp4", sanitizeShareDisplayName("/etc/../evil.mp4", "video/mp4"))
        assertEquals("evil.mp4", sanitizeShareDisplayName("a\\b\\evil.mp4", "video/mp4"))
    }

    @Test
    fun `empty or dot-only name falls back to a safe generic name by mime kind`() {
        assertEquals("shared_file.mp4", sanitizeShareDisplayName("", "video/mp4"))
        assertEquals("shared_file.m4a", sanitizeShareDisplayName("   ", "audio/mp4"))
        assertEquals("shared_file", sanitizeShareDisplayName("..", "application/octet-stream"))
        assertEquals("shared_file", sanitizeShareDisplayName(".", "application/octet-stream"))
    }

    @Test
    fun `control characters and NUL are stripped`() {
        assertEquals("evilmp4", sanitizeShareDisplayName("evil\u0000\u0007mp4", "video/mp4"))
    }
}

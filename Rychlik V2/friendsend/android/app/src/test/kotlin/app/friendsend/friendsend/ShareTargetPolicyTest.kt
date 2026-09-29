package app.friendsend.friendsend

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ShareTargetPolicyTest {
    private fun t(pkg: String, cls: String = "$pkg.Send", label: String = pkg) = RawShareTarget(pkg, cls, label)

    @Test
    fun ownPackageIsNeverOffered() {
        val out = ShareTargetPolicy.select(listOf(t("app.friendsend.friendsend", label = "FriendSend"), t("com.other", label = "Other")), "app.friendsend.friendsend")
        assertEquals(listOf("com.other"), out.map { it.packageName })
    }

    @Test
    fun resultIsDeterministicAlphabeticalByLabel() {
        val out = ShareTargetPolicy.select(listOf(t("p.z", label = "zeta"), t("p.a", label = "Alpha"), t("p.m", label = "Mid")), "own")
        assertEquals(listOf("Alpha", "Mid", "zeta"), out.map { it.label })
    }

    @Test
    fun duplicateComponentsAndSameLabelSamePackageCollapse() {
        val out = ShareTargetPolicy.select(
            listOf(t("p.a", "p.a.One", "App"), t("p.a", "p.a.One", "App"), t("p.a", "p.a.Two", "App"), t("p.a", "p.a.Three", "App Status")),
            "own",
        )
        assertEquals(listOf("App", "App Status"), out.map { it.label })
    }

    @Test
    fun blankLabelFallsBackToPackageName() {
        val out = ShareTargetPolicy.select(listOf(t("com.x.y", label = "  ")), "own")
        assertEquals("com.x.y", out.single().label)
    }

    @Test
    fun socialAppsComeFirstThenAlphabetical_evenWhenTheyWouldBeCutOffByTheLimit() {
        val many = (1..40).map { t("p.app$it", label = "App%02d".format(it)) } + listOf(
            RawShareTarget("z.messenger", "z.messenger.Send", "Zed Messenger", social = true),
            RawShareTarget("y.chat", "y.chat.Send", "Yak Chat", social = true),
        )
        val out = ShareTargetPolicy.select(many, "own")
        assertEquals(listOf("Yak Chat", "Zed Messenger", "App01"), out.take(3).map { it.label })
        assertEquals(ShareTargetPolicy.MAX_TARGETS, out.size)
    }

    @Test
    fun listIsBounded() {
        val many = (1..40).map { t("p.app$it", label = "App%02d".format(it)) }
        assertEquals(ShareTargetPolicy.MAX_TARGETS, ShareTargetPolicy.select(many, "own").size)
        assertEquals(3, ShareTargetPolicy.select(many, "own", max = 3).size)
    }

    @Test
    fun emptyInputGivesEmptyList() {
        assertTrue(ShareTargetPolicy.select(emptyList(), "own").isEmpty())
    }

    @Test
    fun componentIdRoundTripsAndRejectsMalformedInput() {
        assertEquals("a.b" to "a.b.C", parseComponentId(componentId("a.b", "a.b.C")))
        for (bad in listOf(null, "", "nosep", "a/", "/b", "a/b/c", "a b/c", "a/b\nc")) {
            assertNull("should reject '$bad'", parseComponentId(bad))
        }
    }

    @Test
    fun noProviderNamesAreHardCoded() {
        val source = java.io.File("src/main/kotlin/app/friendsend/friendsend/ShareTargets.kt").readText().lowercase()
        for (name in listOf("whatsapp", "telegram", "messenger", "signal", "gmail", "instagram", "facebook")) {
            assertTrue("hard-coded provider: $name", !source.contains(name))
        }
    }
}

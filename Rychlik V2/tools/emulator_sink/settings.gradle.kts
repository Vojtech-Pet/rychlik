pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
plugins { id("com.android.application") version "8.11.1" apply false }
dependencyResolutionManagement { repositories { google(); mavenCentral() } }
include(":app")

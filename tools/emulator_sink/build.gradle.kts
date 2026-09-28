plugins { id("com.android.application") }
val sinkId = (findProperty("sinkId") as String?) ?: "test.sharesink"
val mime = (findProperty("sinkMime") as String?) ?: "video/*"
android {
    namespace = "test.sink"
    compileSdk = 36
    defaultConfig {
        applicationId = sinkId
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1"
        manifestPlaceholders["sinkMime"] = mime
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
}

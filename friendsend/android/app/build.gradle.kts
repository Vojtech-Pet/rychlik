plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
    id("com.chaquo.python")
}

android {
    namespace = "app.friendsend.friendsend"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "app.friendsend.friendsend"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        ndk {
            // Chaquopy ships CPython for these ABIs: real phones (arm64) and the x86_64 emulator.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        // Machine-specific host Python used at build time (e.g. in ~/.gradle/gradle.properties).
        (findProperty("chaquopy.buildPython") as String?)?.let { buildPython(it) }
        pip {
            install("yt-dlp")
        }
    }
}

flutter {
    source = "../.."
}

dependencies {
    // Prompt A17-E1: a plain JVM unit test for sanitizeShareDisplayName()
    // (no Android runtime/emulator needed -- it is a Context-free function).
    testImplementation("junit:junit:4.13.2")
}

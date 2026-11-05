plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Set by the GitHub Actions workflow. Local builds fall back to the debug key.
val buildNumber = System.getenv("VERSION_CODE")?.toIntOrNull() ?: 1
val keystorePath: String? = System.getenv("SIGNING_KEYSTORE_PATH")

android {
    namespace = "com.origindutch.yoink"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.origindutch.yoink"
        minSdk = 29
        targetSdk = 35
        versionCode = buildNumber
        versionName = "1.0.$buildNumber"
    }

    signingConfigs {
        create("release") {
            if (keystorePath != null) {
                storeFile = file(keystorePath)
                storePassword = System.getenv("SIGNING_PASSWORD")
                keyAlias = "yoink"
                keyPassword = System.getenv("SIGNING_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false // keeps the APK easy to inspect
            signingConfig = signingConfigs.getByName(if (keystorePath != null) "release" else "debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    // Don't embed the encrypted dependency report that is only meant for Google Play.
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }
}

dependencies {
    // No Compose, no coroutines: plain Views and threads keep the APK small.
    implementation("androidx.activity:activity-ktx:1.9.3")

    // QR scanner. Uses the camera directly, no Google Play Services / ML Kit.
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
}

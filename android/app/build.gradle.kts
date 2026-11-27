plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "org.unityconnect.health"
    compileSdk = 37
    defaultConfig {
        applicationId = "org.unityconnect.health"
        minSdk = 28
        targetSdk = 37
        versionCode = 4
        versionName = "0.4.1"
    }
    buildFeatures { compose = true; buildConfig = true; aidl = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    signingConfigs.getByName("debug") {
        val migratedKey = rootProject.file(".signing/debug.keystore")
        storeFile = providers.environmentVariable("UNITY_HEALTH_KEYSTORE").orNull?.let { file(it) }
            ?: migratedKey.takeIf { it.isFile }
            ?: file("${System.getProperty("user.home")}/.local/share/unity-build/signing/health.keystore")
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            signingConfig = signingConfigs.getByName("debug")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
        }
    }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2026.09.00"))
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.health.connect:connect-client:1.1.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
}

#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
UNITY_TOOLS="$PROJECT_DIR/../Unity_Android/.tools"

if [ -z "${JAVA_HOME:-}" ] && [ -x "$UNITY_TOOLS/jdk21/bin/java" ]; then
    export JAVA_HOME="$UNITY_TOOLS/jdk21"
fi
if [ -z "${ANDROID_HOME:-}" ] && [ -d "$UNITY_TOOLS/android-sdk" ]; then
    export ANDROID_HOME="$UNITY_TOOLS/android-sdk"
fi
if [ -z "${ANDROID_HOME:-}" ]; then
    echo "ANDROID_HOME must point to an SDK with platform 37 and build tools." >&2
    exit 1
fi
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$PROJECT_DIR/.tools/gradle}"
mkdir -p "$PROJECT_DIR/dist"
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > "$PROJECT_DIR/android/local.properties"
cd "$PROJECT_DIR/android"
if [ -x "$UNITY_TOOLS/gradle-9.6.0/bin/gradle" ]; then
    "$UNITY_TOOLS/gradle-9.6.0/bin/gradle" --no-daemon --console=plain "$@" :app:assembleRelease :app:testDebugUnitTest
else
    ./gradlew --no-daemon --console=plain "$@" :app:assembleRelease :app:testDebugUnitTest
fi
version=$(sed -n 's/^[[:space:]]*versionName = "\([^"]*\)"$/\1/p' app/build.gradle.kts)
apk="$PROJECT_DIR/dist/UnityWellBeing-$version.apk"
cp app/build/outputs/apk/release/app-release.apk "$apk"
echo "Built $apk"

package com.origindutch.yoink

import android.app.Application
import android.content.Context
import android.os.Build
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Keeps the last crash (or caught error) so MainActivity can show it with a
 * copy button. No Android Studio or adb needed to find out what went wrong.
 */
object CrashLog {
    private const val PREFS = "crashlog"
    private const val KEY = "last"

    fun install(app: Application) {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            try {
                save(app, "CRASH in thread '${thread.name}'", error)
            } catch (_: Throwable) {
                // Never make a crash worse.
            }
            previous?.uncaughtException(thread, error)
        }
    }

    /** An error that was caught and handled, but is worth seeing. */
    fun note(ctx: Context, what: String, error: Throwable) {
        try {
            save(ctx.applicationContext, what, error)
        } catch (_: Throwable) {
        }
    }

    fun take(ctx: Context): String? {
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return prefs.getString(KEY, null)
    }

    fun clear(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY).commit()
    }

    private fun save(ctx: Context, what: String, error: Throwable) {
        val trace = StringWriter().also { error.printStackTrace(PrintWriter(it)) }.toString()
        val version = try {
            ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName
        } catch (_: Exception) {
            "?"
        }
        val time = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.ROOT).format(Date())
        val text = buildString {
            appendLine(what)
            appendLine("Yoink $version, Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT}), ${Build.MANUFACTURER} ${Build.MODEL}")
            appendLine(time)
            appendLine()
            append(trace.take(12_000))
        }
        // commit(): the process may be about to die.
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, text).commit()
    }
}

class YoinkApp : Application() {
    override fun onCreate() {
        super.onCreate()
        CrashLog.install(this)
    }
}

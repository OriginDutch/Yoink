package com.origindutch.yoink

import android.content.Context
import org.json.JSONArray

/** Links waiting to reach the PC. Survives app restarts and reboots. */
object LinkStore {
    private const val PREFS = "links"
    private const val KEY = "pending"
    private val lock = Any()

    private val URL_RE = Regex("""https?://[^\s<>"']+""")

    /** Every http(s) link in a blob of shared text, in order, no duplicates. */
    fun extract(text: String?): List<String> {
        if (text.isNullOrBlank()) return emptyList()
        return URL_RE.findAll(text)
            .map { it.value.trimEnd('.', ',', ')', ';', ']') }
            .distinct()
            .toList()
    }

    fun all(ctx: Context): List<String> = synchronized(lock) { read(ctx) }

    fun add(ctx: Context, urls: List<String>) = synchronized(lock) {
        val current = read(ctx).toMutableList()
        urls.forEach { if (it !in current) current.add(it) }
        write(ctx, current)
    }

    fun remove(ctx: Context, urls: Collection<String>) = synchronized(lock) {
        write(ctx, read(ctx).filter { it !in urls })
    }

    fun clear(ctx: Context) = synchronized(lock) { write(ctx, emptyList()) }

    private fun read(ctx: Context): List<String> {
        val raw = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null) ?: return emptyList()
        return try {
            val arr = JSONArray(raw)
            (0 until arr.length()).map { arr.getString(it) }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun write(ctx: Context, urls: List<String>) {
        // commit(), not apply(): the share screen may be gone a moment later.
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY, JSONArray(urls).toString())
            .commit()
    }
}

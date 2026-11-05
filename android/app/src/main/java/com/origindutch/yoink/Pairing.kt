package com.origindutch.yoink

import android.content.Context
import android.net.Uri

/** What the QR code in Yoink on the PC contains. */
data class Pairing(
    val hosts: List<String>,
    val port: Int,
    val token: String,
    val pcName: String,
)

object PairingStore {
    private const val PREFS = "pairing"

    fun load(ctx: Context): Pairing? {
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val token = p.getString("token", null) ?: return null
        val hosts = p.getString("hosts", null)?.split(',')?.filter { it.isNotBlank() } ?: return null
        if (hosts.isEmpty()) return null
        return Pairing(hosts, p.getInt("port", 47824), token, p.getString("pcName", "PC") ?: "PC")
    }

    fun save(ctx: Context, pairing: Pairing) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("token", pairing.token)
            .putString("hosts", pairing.hosts.joinToString(","))
            .putInt("port", pairing.port)
            .putString("pcName", pairing.pcName)
            .apply()
    }

    fun clear(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    }

    /** Remember which address worked, so it is tried first next time. */
    fun preferHost(ctx: Context, host: String) {
        val current = load(ctx) ?: return
        if (current.hosts.firstOrNull() == host) return
        save(ctx, current.copy(hosts = listOf(host) + current.hosts.filter { it != host }))
    }

    /** yoink://pair?v=1&h=192.168.1.10,10.0.0.5&p=47824&t=TOKEN&n=PCNAME */
    fun parse(text: String): Pairing? {
        val uri = try { Uri.parse(text.trim()) } catch (_: Exception) { return null }
        if (uri.scheme != "yoink" || uri.host != "pair") return null
        val hosts = uri.getQueryParameter("h")?.split(',')?.map { it.trim() }?.filter { it.isNotEmpty() }
        val port = uri.getQueryParameter("p")?.toIntOrNull()
        val token = uri.getQueryParameter("t")
        if (hosts.isNullOrEmpty() || port == null || token.isNullOrBlank()) return null
        return Pairing(hosts, port, token, uri.getQueryParameter("n") ?: "PC")
    }
}

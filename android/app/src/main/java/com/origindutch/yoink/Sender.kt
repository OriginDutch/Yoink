package com.origindutch.yoink

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import kotlin.concurrent.thread

/** Sends everything in [LinkStore] to the PC. Safe to call from any thread. */
object Sender {

    enum class Result { SENT, NOTHING, NOT_PAIRED, OFFLINE, UNAUTHORIZED }

    data class Outcome(val result: Result, val count: Int = 0, val pcName: String? = null)

    private val lock = Any()

    fun flush(ctx: Context): Outcome {
        val app = ctx.applicationContext
        synchronized(lock) {
            try {
                return attempt(app)
            } catch (e: Exception) {
                // Anything unexpected: keep the links, report "later", don't crash.
                CrashLog.note(app, "Versturen mislukt", e)
                return Outcome(Result.OFFLINE, LinkStore.all(app).size)
            } finally {
                RetryJob.update(app)
            }
        }
    }

    private fun attempt(app: Context): Outcome {
        val pending = LinkStore.all(app)
        if (pending.isEmpty()) return Outcome(Result.NOTHING)
        val pairing = PairingStore.load(app) ?: return Outcome(Result.NOT_PAIRED, pending.size)
        val api = YoinkApi(pairing)
        return try {
            val name = api.connect()
            api.sendLinks(pending)
            LinkStore.remove(app, pending)
            api.workingHost?.let { PairingStore.preferHost(app, it) }
            Outcome(Result.SENT, pending.size, name)
        } catch (e: HttpStatusException) {
            if (e.code == 401) Outcome(Result.UNAUTHORIZED, pending.size) else Outcome(Result.OFFLINE, pending.size)
        } catch (e: Exception) {
            Outcome(Result.OFFLINE, pending.size)
        }
    }

    /** Short line for a toast or the status text. */
    fun describe(o: Outcome): String = when (o.result) {
        Result.SENT -> if (o.count == 1) "Yoinked ✅" else "${o.count} links naar Yoink ✅"
        Result.NOTHING -> "Niks te versturen."
        Result.NOT_PAIRED -> "Bewaard 📦 Koppel eerst met Yoink op je PC."
        Result.OFFLINE -> "Bewaard 📦 Yoink staat uit of je zit niet op de thuiswifi. Volgt later."
        Result.UNAUTHORIZED -> "Bewaard 📦 Yoink kent deze telefoon niet meer. Scan de QR-code opnieuw."
    }
}

/**
 * While links are waiting, Android runs [RetryJobService] about every 15 minutes
 * (the minimum it allows) when on wifi. Cancelled again once the queue is empty.
 */
object RetryJob {
    private const val ID = 1
    private const val PERIOD_MS = 15 * 60 * 1000L

    /** Never throws: a scheduling problem must not take the app down. */
    fun update(ctx: Context) {
        try {
            schedule(ctx)
        } catch (e: Exception) {
            CrashLog.note(ctx, "Retry-job inplannen mislukt", e)
        }
    }

    private fun schedule(ctx: Context) {
        val js = ctx.getSystemService(JobScheduler::class.java) ?: return
        val needed = LinkStore.all(ctx).isNotEmpty() && PairingStore.load(ctx) != null
        if (!needed) {
            if (js.getPendingJob(ID) != null) js.cancel(ID)
            return
        }
        if (js.getPendingJob(ID) != null) return
        val job = JobInfo.Builder(ID, ComponentName(ctx, RetryJobService::class.java))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED)
            .setPeriodic(PERIOD_MS)
            .setPersisted(true)
            .build()
        js.schedule(job)
    }
}

class RetryJobService : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        thread(name = "yoink-retry") {
            Sender.flush(applicationContext)
            jobFinished(params, false)
        }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean = true
}

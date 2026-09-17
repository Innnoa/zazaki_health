package com.zazaki.healthreader

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import java.time.LocalDate

/**
 * 未传数据提醒通知（打开 app 时 + 每日闹钟 Receiver 共用）。
 * Channel: health_sync_pending（notif_channel_name / notif_channel_desc），
 * 小图标 R.drawable.ic_stat_sync（纯白 alpha）。
 */
object PendingNotifier {

    const val CHANNEL_ID = "health_sync_pending"
    const val NOTIF_ID = 1001

    fun ensureChannel(context: Context) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val nm = context.getSystemService(NotificationManager::class.java) ?: return
                val channel = NotificationChannel(
                    CHANNEL_ID,
                    context.getString(R.string.notif_channel_name),
                    NotificationManager.IMPORTANCE_DEFAULT
                )
                channel.description = context.getString(R.string.notif_channel_desc)
                nm.createNotificationChannel(channel)
            }
        } catch (t: Throwable) {
            // 通知不可用不影响主功能
        }
    }

    fun canNotify(context: Context): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            try {
                context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
                    PackageManager.PERMISSION_GRANTED
            } catch (t: Throwable) {
                false
            }
        } else {
            true
        }
    }

    /** 发布「有 N 天未同步」通知；点击回到 MainActivity。 */
    fun post(context: Context, pendingN: Int) {
        try {
            ensureChannel(context)
            if (!canNotify(context)) return
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            val pi = PendingIntent.getActivity(
                context,
                0,
                Intent(context, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                Notification.Builder(context, CHANNEL_ID)
            } else {
                @Suppress("DEPRECATION")
                Notification.Builder(context)
            }
            val notification = builder
                .setSmallIcon(R.drawable.ic_stat_sync)
                .setContentTitle(context.getString(R.string.notif_title_pending))
                .setContentText(context.getString(R.string.notif_text_pending, pendingN))
                .setContentIntent(pi)
                .setAutoCancel(true)
                .build()
            nm.notify(NOTIF_ID, notification)
        } catch (t: Throwable) {
            // 不崩溃
        }
    }
}

/**
 * 每日提醒调度：一次性精确闹钟 + Receiver 触发后自续排下一天。
 * 不用 setInexactRepeating —— 该设备上系统给重复闹钟 ~18h 的 slack 窗口，且后台冻结会压制，
 * 导致每日提醒实际永不触发。改为：
 *  - API 31+ 且 SCHEDULE_EXACT_ALARM 可用 → setExactAndAllowWhileIdle（接近准点触发）；
 *  - 否则（无精确闹钟授权）→ setWindow(RTC_WAKEUP, trigger, 15min 窗口)（无需授权）；
 *  - 绝不使用 setInexactRepeating / setRepeating。
 * 所有失败静默降级为 Log.w，绝不崩溃。
 */
object ReminderScheduler {

    const val PREFS = "app_prefs"
    const val KEY_REMINDER_HHMM = "reminder_hhmm"
    const val REQUEST_CODE = 2001

    private const val FALLBACK_WINDOW_MS = 15L * 60L * 1000L
    private const val MIN_AHEAD_MS = 60_000L // 提前触发保护：距目标不足 1 分钟则顺延次日，避免触发后紧贴重排造成重入

    /** 读偏好里的 HH:mm，排下一次一次性闹钟；未设置时间时静默返回。 */
    fun scheduleNext(context: Context) {
        try {
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val hhmm = prefs.getString(KEY_REMINDER_HHMM, null) ?: return
            val parts = hhmm.split(":")
            if (parts.size != 2) return
            val hour = parts[0].toInt()
            val minute = parts[1].toInt()
            val am = context.getSystemService(AlarmManager::class.java) ?: return
            val pi = PendingIntent.getBroadcast(
                context,
                REQUEST_CODE,
                Intent(context, ReminderReceiver::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val triggerAt = nextTriggerMillis(hour, minute)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && am.canScheduleExactAlarms()) {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAt, pi)
            } else {
                am.setWindow(AlarmManager.RTC_WAKEUP, triggerAt, FALLBACK_WINDOW_MS, pi)
            }
        } catch (t: Throwable) {
            android.util.Log.w(
                "HealthReader",
                "ReminderScheduler.scheduleNext failed: ${t.javaClass.simpleName}: ${t.message}"
            )
        }
    }

    /** 下一次 HH:mm 触发时刻：严格晚于 now（加 1 分钟保护带）。 */
    private fun nextTriggerMillis(hour: Int, minute: Int): Long {
        val cal = java.util.Calendar.getInstance()
        cal.set(java.util.Calendar.HOUR_OF_DAY, hour)
        cal.set(java.util.Calendar.MINUTE, minute)
        cal.set(java.util.Calendar.SECOND, 0)
        cal.set(java.util.Calendar.MILLISECOND, 0)
        if (cal.timeInMillis <= System.currentTimeMillis() + MIN_AHEAD_MS) {
            cal.add(java.util.Calendar.DAY_OF_YEAR, 1)
        }
        return cal.timeInMillis
    }
}

/**
 * 每日定时提醒 Receiver（由 ReminderScheduler 的一次性精确闹钟触发，在 AndroidManifest 声明）。
 * 到点后读取 sync_state.json，若存在 date < today 的 pending 日则发一条提醒通知；
 * 随后无论如何都重排下一次（自续排，保证明天同一时间仍会触发）。
 * 任何失败都不得导致崩溃。
 */
class ReminderReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent?) {
        try {
            val state = SyncStateStore.load(context)
            val n = SyncStateStore.pendingCountBefore(state, LocalDate.now())
            if (n > 0) {
                PendingNotifier.post(context, n)
            }
        } catch (t: Throwable) {
            // 通知失败静默
        }
        // 自续排下一天（无论是否有 pending，闹钟都要保持每日触发）
        ReminderScheduler.scheduleNext(context)
    }
}

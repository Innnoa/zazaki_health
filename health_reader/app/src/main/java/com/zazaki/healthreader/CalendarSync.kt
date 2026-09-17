package com.zazaki.healthreader

import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.SharedPreferences
import android.provider.CalendarContract
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset

/**
 * 系统日历联动（CalendarContract）。
 *
 * 设计：
 *  - 专属日历：ACCOUNT_NAME = "com.zazaki.healthreader"，ACCOUNT_TYPE = ACCOUNT_TYPE_LOCAL，
 *    显示名来自 strings（app_name = 健康数据同步）。日历 id 持久化在 SharedPreferences("calendar_prefs")。
 *  - 幂等键：查询时以 (CALENDAR_ID + 事件 TITLE 包含 "yyyy-MM-dd") 匹配当日事件 —— 事件标题由
 *    strings 的 cal_event_pending / cal_event_done + ISO 日期构成（"待同步 2026-09-06" / "✅ 已同步 2026-09-06"）。
 *    标题内嵌完整日期，天然是 (日历, 日期) 的稳定键；当日事件存在则更新标题，多余重复事件中立化，不存在则插入。
 *  - 「移除」语义：本机 Samsung Calendar provider 对 Events 表的物理 delete 静默无效（返回 rows=1 但行仍在），
 *    因此清理一律走 UPDATE 中立化（标题换成无日期键的中性串 + 时间移到远古），不使用 Events delete。
 *  - 事件形态：all-day 事件（ALL_DAY=1，DTSTART/DTEND 采用 UTC 午夜 —— Android 标准 all-day 语义，
 *    EVENT_TIMEZONE=系统时区）。Samsung provider 会按「UTC 日期」归一化 all-day 事件时间，
 *    若用本地午夜插入（如 +08 的 09-06 00:00 → UTC 09-05T16:00Z）事件会显示提前一天；
 *    故统一按目标日期的 UTC 00:00 提交。
 *  - 所有操作 try/catch 静默失败：日历不可用时 app 照常工作。
 */
object CalendarSync {

    const val ACCOUNT = "com.zazaki.healthreader"

    private const val PREFS = "calendar_prefs"
    private const val KEY_CAL_ID = "calendar_id"

    /** 事件标题内嵌的日期键（yyyy-MM-dd），幂等/清理共用。 */
    private val ISO_IN_TITLE = Regex("\\d{4}-\\d{2}-\\d{2}")

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun getCalId(context: Context): Long? {
        val v = prefs(context).getLong(KEY_CAL_ID, -1L)
        return if (v > 0L) v else null
    }

    private fun setCalId(context: Context, id: Long) {
        prefs(context).edit().putLong(KEY_CAL_ID, id).apply()
    }

    fun isPermGranted(context: Context): Boolean {
        return android.Manifest.permission.READ_CALENDAR in granted(context) &&
                android.Manifest.permission.WRITE_CALENDAR in granted(context)
    }

    private fun granted(context: Context): Set<String> {
        val out = HashSet<String>()
        for (p in arrayOf(
            android.Manifest.permission.READ_CALENDAR,
            android.Manifest.permission.WRITE_CALENDAR
        )) {
            if (androidx.core.content.ContextCompat.checkSelfPermission(context, p) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED
            ) {
                out.add(p)
            }
        }
        return out
    }

    /**
     * 确保专属日历存在，返回其 _ID（找不到则新建）。调用前需已授予日历读写权限。
     */
    fun ensureCalendar(context: Context, displayName: String): Long? {
        return try {
            val resolver = context.contentResolver

            // 1) 已保存 id 仍存在 → 直接复用
            getCalId(context)?.let { saved ->
                val uri = ContentUris.withAppendedId(CalendarContract.Calendars.CONTENT_URI, saved)
                resolver.query(uri, arrayOf(CalendarContract.Calendars._ID), null, null, null)?.use { c ->
                    if (c.moveToFirst()) return c.getLong(0)
                }
            }

            // 2) 按 account 查询
            resolver.query(
                CalendarContract.Calendars.CONTENT_URI,
                arrayOf(CalendarContract.Calendars._ID),
                "${CalendarContract.Calendars.ACCOUNT_NAME}=? AND ${CalendarContract.Calendars.ACCOUNT_TYPE}=?",
                arrayOf(ACCOUNT, CalendarContract.ACCOUNT_TYPE_LOCAL),
                null
            )?.use { c ->
                if (c.moveToFirst()) {
                    val id = c.getLong(0)
                    setCalId(context, id)
                    return id
                }
            }

            // 3) 新建本地日历
            val values = ContentValues().apply {
                put(CalendarContract.Calendars.ACCOUNT_NAME, ACCOUNT)
                put(CalendarContract.Calendars.ACCOUNT_TYPE, CalendarContract.ACCOUNT_TYPE_LOCAL)
                put(CalendarContract.Calendars.NAME, ACCOUNT)
                put(CalendarContract.Calendars.CALENDAR_DISPLAY_NAME, displayName)
                put(CalendarContract.Calendars.OWNER_ACCOUNT, ACCOUNT)
                put(CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL, CalendarContract.Calendars.CAL_ACCESS_OWNER)
                put(CalendarContract.Calendars.CALENDAR_COLOR, 0xFF0F766E.toInt())
                put(CalendarContract.Calendars.VISIBLE, 1)
                put(CalendarContract.Calendars.SYNC_EVENTS, 0)
            }
            val newUri = resolver.insert(CalendarContract.Calendars.CONTENT_URI, values) ?: return null
            val id = ContentUris.parseId(newUri)
            setCalId(context, id)
            id
        } catch (t: Throwable) {
            null
        }
    }

    /** 目标日期的事件标题（含 ISO 日期，作为幂等键）。 */
    private fun titleFor(context: Context, date: LocalDate, done: Boolean): String {
        val res = if (done) R.string.cal_event_done else R.string.cal_event_pending
        return context.getString(res, date.toString())
    }

    /**
     * 幂等 upsert：为 (calendarId, 本地日期) 维护一条 all-day 事件。
     * done=true → 「✅ 已同步 yyyy-MM-dd」；done=false → 「待同步 yyyy-MM-dd」。
     * 日期语义：DTSTART/DTEND 用目标日期的 UTC 午夜（Android 标准 all-day），避免显示提前一天。
     * 已存在的事件会顺带修正 DTSTART/DTEND（历史用本地午夜插入的错位事件自愈）。
     */
    fun upsertDayEvent(context: Context, calId: Long, date: LocalDate, done: Boolean) {
        try {
            val resolver = context.contentResolver
            val iso = date.toString()
            val title = titleFor(context, date, done)
            // Android 标准 all-day：以 UTC 日期午夜为 dtstart（provider 按 UTC 日期归一化）
            val startMillis = date.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
            val endMillis = date.plusDays(1).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()

            // 查询该日历下标题包含该日期的所有事件（即该日的事件，不分 pending/done）
            var firstId = -1L
            resolver.query(
                CalendarContract.Events.CONTENT_URI,
                arrayOf(CalendarContract.Events._ID),
                "${CalendarContract.Events.CALENDAR_ID}=? AND ${CalendarContract.Events.TITLE} LIKE ?",
                arrayOf(calId.toString(), "%$iso%"),
                null
            )?.use { c ->
                while (c.moveToNext()) {
                    val id = c.getLong(0)
                    if (firstId < 0L) {
                        firstId = id
                    } else {
                        // 同一天出现多条 → 中立化多余行（本 provider 对 Events 物理 delete 静默无效）
                        neutralizeEventRow(context, id)
                    }
                }
            }

            if (firstId > 0L) {
                // 更新标题；同时修正 DTSTART/DTEND，把历史错位（提前一天）的事件挪回正确日期
                val cv = ContentValues().apply {
                    put(CalendarContract.Events.TITLE, title)
                    put(CalendarContract.Events.DTSTART, startMillis)
                    put(CalendarContract.Events.DTEND, endMillis)
                }
                resolver.update(
                    CalendarContract.Events.CONTENT_URI,
                    cv,
                    "${CalendarContract.Events._ID}=?",
                    arrayOf(firstId.toString())
                )
                return
            }

            // 不存在 → 插入 all-day 事件（UTC 午夜语义）
            val values = ContentValues().apply {
                put(CalendarContract.Events.CALENDAR_ID, calId)
                put(CalendarContract.Events.TITLE, title)
                put(CalendarContract.Events.DTSTART, startMillis)
                put(CalendarContract.Events.DTEND, endMillis)
                put(CalendarContract.Events.ALL_DAY, 1)
                put(CalendarContract.Events.EVENT_TIMEZONE, ZoneId.systemDefault().id)
            }
            resolver.insert(CalendarContract.Events.CONTENT_URI, values)
        } catch (t: Throwable) {
            // 日历写入失败静默跳过；下次打开/上传成功后会再同步
        }
    }

    /**
     * 「移除」某日事件（幂等键同 upsertDayEvent：标题包含 yyyy-MM-dd）。
     * 注意：本机 Samsung Calendar provider 对 Events 表的物理 delete 静默无效（返回 rows=1 但行仍在），
     * 故此处用 UPDATE 中立化实现移除：标题改为不含日期键的中性串、时间移到远古，
     * 事件不再可见，也不再被 upsert/sweep/delete 的任何查询匹配。
     */
    fun deleteDayEvent(context: Context, calId: Long, date: LocalDate) {
        try {
            val resolver = context.contentResolver
            val iso = date.toString()
            resolver.query(
                CalendarContract.Events.CONTENT_URI,
                arrayOf(CalendarContract.Events._ID),
                "${CalendarContract.Events.CALENDAR_ID}=? AND ${CalendarContract.Events.TITLE} LIKE ?",
                arrayOf(calId.toString(), "%$iso%"),
                null
            )?.use { c ->
                while (c.moveToNext()) {
                    neutralizeEventRow(context, c.getLong(0))
                }
            }
        } catch (t: Throwable) {
            // 静默失败；下次打开会再尝试
        }
    }

    /**
     * 清理本日历下「标题日期不在 activeIso（当前 sent/pending 日期集）」的事件：
     * 逐条读 TITLE、正则提取 yyyy-MM-dd，日期缺失于 activeIso 即中立化该行
     * （update 墓碑；本 provider 的物理 delete 无效）。可覆盖展示窗口之外的历史残留
     * （如 09-05 已降级 nodata 但窗口已收到 09-06 起的旧事件）。
     * 标题中不含 ISO 日期的事件（非本 app 键约定 / 已中立化）保留不动。静默失败。
     */
    fun neutralizeEventsNotActive(context: Context, calId: Long, activeIso: Set<String>) {
        try {
            val resolver = context.contentResolver
            resolver.query(
                CalendarContract.Events.CONTENT_URI,
                arrayOf(CalendarContract.Events._ID, CalendarContract.Events.TITLE),
                "${CalendarContract.Events.CALENDAR_ID}=?",
                arrayOf(calId.toString()),
                null
            )?.use { c ->
                while (c.moveToNext()) {
                    val id = c.getLong(0)
                    val title = c.getString(1) ?: continue
                    val iso = ISO_IN_TITLE.find(title)?.value
                    if (iso != null && !activeIso.contains(iso)) {
                        neutralizeEventRow(context, id)
                    }
                }
            }
        } catch (t: Throwable) {
            // 静默失败；下次打开会再尝试
        }
    }

    /** 把事件行更新为中性墓碑：标题无 ISO 日期与关键词，时间移到远古，任何查询都无法再匹配。 */
    private fun neutralizeEventRow(context: Context, id: Long) {
        try {
            val clearedTitle = context.getString(R.string.cal_event_cleared)
            val farPastStart = LocalDate.of(2000, 1, 1).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
            val farPastEnd = LocalDate.of(2000, 1, 2).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
            val cv = ContentValues().apply {
                put(CalendarContract.Events.TITLE, clearedTitle)
                put(CalendarContract.Events.DTSTART, farPastStart)
                put(CalendarContract.Events.DTEND, farPastEnd)
            }
            context.contentResolver.update(
                CalendarContract.Events.CONTENT_URI,
                cv,
                "${CalendarContract.Events._ID}=?",
                arrayOf(id.toString())
            )
        } catch (t: Throwable) {
            // 静默失败
        }
    }
}

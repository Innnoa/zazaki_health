package com.zazaki.healthreader

import android.content.Context
import com.google.gson.Gson
import java.io.File
import java.time.LocalDate

/**
 * 每日同步状态机持久化（app 内部文件 sync_state.json）。
 *
 * Schema:
 * {
 *   "states":        { "yyyy-MM-dd": "sent"|"pending"|"nodata", ... },
 *   "lastUpdatedAt": "yyyy-MM-ddTHH:mm:ss[.n]" (LocalDateTime.toString(), 最近一次状态写入),
 *   "lastSentAt":    "yyyy-MM-ddTHH:mm:ss[.n]" | null (最近一次成功上传时间)
 * }
 *
 * Semantics:
 *  - "sent":    该日数据已成功 POST 到电脑端（幂等，绝不重发）。
 *  - "pending": 该日（今天之前）有数据且未传 → 计入待传 N；今天的 pending 不计入 N，仅显示「待同步」。
 *  - "nodata":  该日无数据（灰显）。扫描时若发现数据消失，pending 会降级为 nodata；
 *               一旦某日标记 sent，永不回退。
 *
 * 清理规则（每次扫描重建时执行）：
 *  - 只保留展示窗口（windowStart..today）内所有日期的状态；
 *  - 窗口之外的旧记录仅保留 "sent"（用于总传输数 / 连续天数统计），丢弃窗口外 pending/nodata。
 *  - 窗口内日期以本次扫描结果覆盖。
 *
 * 写入采用 临时文件 + rename 的原子替换，避免 Activity 与 ReminderReceiver 并发读到半截文件。
 */
object SyncStateStore {

    const val ST_SENT = "sent"
    const val ST_PENDING = "pending"
    const val ST_NODATA = "nodata"

    const val FILE_NAME = "sync_state.json"

    /** Gson 需要无参构造；字段缺失时保持默认值。 */
    class StateFile {
        var states: MutableMap<String, String> = HashMap()
        var lastUpdatedAt: String? = null
        var lastSentAt: String? = null
    }

    private fun file(context: Context): File = File(context.filesDir, FILE_NAME)

    fun load(context: Context): StateFile {
        val f = file(context)
        return try {
            if (f.exists()) {
                Gson().fromJson(f.readText(), StateFile::class.java) ?: StateFile()
            } else {
                StateFile()
            }
        } catch (t: Throwable) {
            StateFile()
        }
    }

    fun save(context: Context, state: StateFile) {
        try {
            val f = file(context)
            val tmp = File(context.filesDir, "$FILE_NAME.tmp")
            tmp.writeText(Gson().toJson(state))
            if (f.exists()) {
                //noinspection ResultOfMethodCallIgnored
                f.delete()
            }
            if (!tmp.renameTo(f)) {
                // rename 失败（极端情况）时直接覆盖写入
                f.writeText(Gson().toJson(state))
                //noinspection ResultOfMethodCallIgnored
                tmp.delete()
            }
        } catch (t: Throwable) {
            // 状态持久化失败不应导致崩溃；下次扫描会重建。
        }
    }

    fun keyOf(date: LocalDate): String = date.toString()

    fun parseKey(key: String): LocalDate? = try {
        LocalDate.parse(key)
    } catch (t: Throwable) {
        null
    }

    /** 统计 store 中 date < today 的 pending 数量（ReminderReceiver 使用，无需 SHealth）。 */
    fun pendingCountBefore(state: StateFile, today: LocalDate): Int {
        var n = 0
        for ((k, v) in state.states) {
            if (v != ST_PENDING) continue
            val d = parseKey(k) ?: continue
            if (d.isBefore(today)) n++
        }
        return n
    }
}

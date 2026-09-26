-- 导出所有硬件计数器采样点
SELECT ct.name AS counter, c.ts AS ts, c.value AS value
FROM counter c
JOIN counter_track ct ON c.track_id = ct.id
ORDER BY ct.name, c.ts;

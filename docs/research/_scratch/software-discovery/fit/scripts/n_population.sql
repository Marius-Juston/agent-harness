-- Stratum N population (PROTOCOL.md amendment 1, rule 9). Run against https://sql-clickhouse.clickhouse.com/?user=demo
WITH first AS (
    SELECT name, min(upload_time) AS first_upload, argMax(version, upload_time) AS latest
    FROM pypi.projects GROUP BY name HAVING first_upload >= '2026-07-15 00:00:00'
),
wheel AS (
    SELECT p.name AS name, max(lengthUTF8(p.description)) AS desc_len
    FROM pypi.projects AS p INNER JOIN first AS f ON p.name = f.name AND p.version = f.latest
    WHERE p.packagetype = 'bdist_wheel' AND endsWith(p.filename, '-py3-none-any.whl')
    GROUP BY p.name
),
dl AS (
    SELECT project AS name, sum(count) AS downloads FROM pypi.pypi_downloads_per_day
    WHERE date >= '2026-07-15' AND date <= '2026-10-12' GROUP BY project
)
SELECT f.name, f.first_upload, f.latest, w.desc_len, d.downloads,
       cityHash64(concat(f.name, '196')) AS h
FROM first AS f INNER JOIN wheel AS w ON f.name = w.name INNER JOIN dl AS d ON f.name = d.name
WHERE w.desc_len >= 1500 AND d.downloads >= 2000
ORDER BY h ASC
FORMAT TSVWithNames

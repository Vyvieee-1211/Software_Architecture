param([int]$Repeat = 3, [int]$LocustWorkers = 4)
$ErrorActionPreference = 'Stop'
# Chạy từ thư mục gốc; DATABASE_URL phải trỏ tới PostgreSQL thử nghiệm đang chạy.
foreach ($poolSize in @(5, 10, 20)) {
    foreach ($workload in @('flash', 'funnel')) {
        & .\.venv\Scripts\python.exe -m loadtest.run S9 --workload $workload `
            --db-pool-size $poolSize --start-api --prepare `
            --locust-workers $LocustWorkers --repeat $Repeat
        if ($LASTEXITCODE -ne 0) { throw "S9 thất bại: workload=$workload pool=$poolSize" }
    }
}

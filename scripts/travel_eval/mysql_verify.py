"""Provision disposable schemas on a dedicated test MySQL 8 instance; retain them for inspection."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
import os
from pathlib import Path
import sys
import uuid
import pymysql
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from mysql_store import MySQLStore,mysql_settings
import travel_store


def migrate(conn,path):
    with conn.cursor() as c:
        sql="\n".join(line for line in Path(path).read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("--"))
        for statement in sql.split(";"):
            if statement.strip():
                c.execute(statement)
    conn.commit()


def main():
    if os.getenv("OSINT_TEST_MYSQL")!="1":
        raise SystemExit("需要专用测试库及 OSINT_TEST_MYSQL=1")
    settings=mysql_settings()
    settings.pop("database")
    for upgraded in (False,True):
        name="travel_test_"+uuid.uuid4().hex[:12]
        with pymysql.connect(**settings) as conn:
            with conn.cursor() as c:
                c.execute("CREATE DATABASE "+name+" CHARACTER SET utf8mb4")
            conn.select_db(name)
            migrate(conn,"deploy/mysql/001_initial.sql")
            if upgraded:
                with conn.cursor() as c:
                    c.execute("INSERT INTO osint_users(username,password_hash) VALUES ('legacy','fixture')")
                    c.execute("""INSERT INTO osint_runs(id,owner_id,task,model_id,status,execution_mode,search_strategy,created_at,updated_at)
                              VALUES('old',1,'历史任务','fixture','done','auto','legacy',NOW(),NOW())""")
                    c.execute("INSERT INTO osint_run_artifacts(run_id,category,filename,content) VALUES ('old','reports','old.md','legacy-report')")
                conn.commit()
            migrate(conn,"deploy/mysql/002_travel.sql")
            migrate(conn,"deploy/mysql/002_travel.sql")
        store=MySQLStore(dict(settings,database=name))
        store.check_schema()
        assert store.healthy()
        if upgraded:
            assert store.read_reports("old")["old.md"]=="legacy-report"
            assert travel_store.account_data(store,1,"credits")["available"]==1
        uid=store.create_user("newuser","fixture",b"1"*32,datetime.now(timezone.utc)+timedelta(days=1))["id"]
        now=datetime.now(timezone.utc).isoformat()
        def reserve(i):
            state={"id":uuid.uuid4().hex,"task":"test","model_id":"m","status":"pending","execution_mode":"auto",
                   "search_strategy":"legacy","created_at":now,"updated_at":now}
            return travel_store.reserve(store,uid,state,"key","f"*64,{"attachment_ids":[]},False,10)
        with ThreadPoolExecutor(6) as pool:
            ids=list(pool.map(reserve,range(6)))
        assert len(set(ids))==1
        def bal():return travel_store.account_data(store,uid,"credits")
        assert bal()==dict(available=0,reserved=1,used=0)
        travel_store.transition(store,uid,ids[0],"released")
        travel_store.transition(store,uid,ids[0],"reserved")
        travel_store.transition(store,uid,ids[0],"used")
        for outcome in ("failed","cancelled","success"):
            oid=travel_store.order(store,uid,"travel-one")["order_id"]
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda _:travel_store.order(store,uid,order_id=oid,outcome=outcome),range(4)))
        assert bal()==dict(available=1,reserved=0,used=1)
        print("PASS", "v1 upgrade" if upgraded else "fresh", name,flush=True)


if __name__=="__main__":
    main()

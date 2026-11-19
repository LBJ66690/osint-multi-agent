"""运行本地回归并保存逐用例结果；外部实测与浏览器联调另行执行。"""
import json,sys,time,unittest,logging
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config
class Result(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.passed=[]
    def addSuccess(self,test):super().addSuccess(test);self.passed.append(test.id())
def main():
    target=config.OUTPUT_DIR/'acceptance';target.mkdir(exist_ok=True)
    for handler in config.logger.handlers[:]:config.logger.removeHandler(handler);handler.close()
    config.logger.addHandler(logging.FileHandler(target/'application.log',encoding='utf-8'))
    start=time.monotonic();suite=unittest.defaultTestLoader.discover(str(config.ROOT/'tests'))
    with (target/'unittest.log').open('w',encoding='utf-8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=Result).run(suite)
    summary={'tests':result.testsRun,'passed':len(result.passed),'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'seconds':round(time.monotonic()-start,2),'passed_cases':result.passed,'failed_cases':[t.id() for t,_ in result.failures+result.errors]}
    (target/'unit_results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'passed_cases','failed_cases'}},ensure_ascii=False),flush=True)
    return 0 if result.wasSuccessful() else 1
if __name__=='__main__':sys.exit(main())

# -*- coding: utf-8 -*-
'''
Created on 2014年11月18日

@author: Leo
'''
import win32serviceutil
import win32service
import win32event
import logging.handlers
import os, sys
import zipfile, shutil
import time
import glob
import re, ntpath
import configparser
import urllib.request
import codecs
from bs4 import BeautifulSoup

class OOMMonitor(win32serviceutil.ServiceFramework):
    '''
    Monitoring Java memory leak by java log file.

    python OOMMonitor.py install                 安装服务
    python OOMMonitor.py --startup auto install  让服务自动启动
    python OOMMonitor.py start                   启动服务
    python OOMMonitor.py restart                 重启服务
    python OOMMonitor.py stop                    停止服务
    python PythonService.py remove               删除/卸载服务
    '''
    # 服务名  
    _svc_name_ = "OOMMonitor"  
    # 服务显示名称  
    _svc_display_name_ = "OOMMonitor"  
    # 服务描述  
    _svc_description_ = "Monitoring Java memory leak by java log file."  


    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        # Create an event which we will use to wait on.
        # The "service stop" request will set this event.
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.isAlive = True
        
        # 基本配置
        self.version = 1.1
        self.nis_version = "12.7.0.3"  # nis版本
        self.breaktime = 2400  # while最长循环时间
        self.oompatterns = "java.lang.OutOfMemoryError"  # 内存溢出检测模式，| 分隔
        self.servicename = "nis"  # 监测服务名
        self.basedir = ""  # java日志记录目录
        self.std_log = 'stdout_*.log'  # 日志文件名格式
        self.hprof = '*.hprof'
        self.backupname = ""  # 压缩文件路径和名字前缀，用于java日志备份
        self.imagename = ""
        self.oomrunurl = ""
        
        # 日志配置 若为0 则只产生一个日志文件，且backupCount失效
        self.maxMegabytes = 10
        self.backupCount = 5
        
        self.logger = self._getLogger()
       
    def SvcStop(self):
        # Before we do anything, tell the SCM we are starting the stop process.
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        # And set my event.
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        self.logger.info("Service core is running!")
        try:
            self.initconfig()
            self.main()
        except Exception as e:
            self.logger.error("Service core is dumped!%s\n==================================" % e)
            sys.exit(-1)
        else:
            self.logger.info("Service core is finished!\n==================================")
            sys.exit(0)
        # We do nothing other than wait to be stopped!
        win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)
    
    '''
    description:日志初始化
    '''
    def _getLogger(self):   
          
        logger = logging.getLogger('[OOMMonitor]')  
          
        handler = logging.handlers.RotatingFileHandler(os.path.join(os.path.split(sys.path[0])[0], "oommonitor.log"), maxBytes=int(self.maxMegabytes) * 1024 * 1024, backupCount=int(self.backupCount))        
        formatter = logging.Formatter('%(asctime)s %(name)-12s %(levelname)-8s %(message)s', '%Y-%m-%d %H:%M:%S')  
        handler.setFormatter(formatter)  
          
        logger.addHandler(handler)  
        logger.setLevel(logging.INFO)  
          
        return logger     
        
    '''
    description:压缩目录后并清空
    parameter：dirname  待压缩的目录
    paramete：zipfilename 压缩后文件名（包含路径）
    ''' 
    def zip_dir(self, dirname, zipfilename):
        self.logger.info("待压缩目录为：%s  压缩后的文件名为：%s" % (dirname, zipfilename))
        if os.path.exists(dirname):
            filelist = []
            if os.path.isfile(dirname):
                filelist.append(dirname)
            else :
                for root, dirs, files in os.walk(dirname):
                    for name in files:
                        filelist.append(os.path.join(root, name))
                 
            zf = zipfile.ZipFile(zipfilename, "w", zipfile.zlib.DEFLATED)
            for tar in filelist:
                arcname = tar[len(dirname):]
                zf.write(tar, arcname)
            zf.close()
            self.logger.info("压缩完成，开始清空目录：%s" % dirname)
        
            shutil.rmtree(dirname, True, None)
            self.logger.info("目录%s清空完成" % dirname)
        else:
            self.logger.info("目录%s不存在" % dirname)
    
    '''
    description:解压zip文件
    parameter：zipfilename 压缩文件
    paramete：unziptodir压缩路径
    '''
    def unzip_file(self, zipfilename, unziptodir):
        if not os.path.exists(unziptodir): os.mkdir(unziptodir, 0o777)
        zfobj = zipfile.ZipFile(zipfilename)
        for name in zfobj.namelist():
            name = name.replace('\\', '/')
            
            if name.endswith('/'):
                os.mkdir(os.path.join(unziptodir, name))
            else:           
                ext_filename = os.path.join(unziptodir, name)
                ext_dir = os.path.dirname(ext_filename)
                if not os.path.exists(ext_dir) : os.mkdir(ext_dir, 0o777)
                outfile = open(ext_filename, 'wb')
                outfile.write(zfobj.read(name))
                outfile.close()
    
    '''
    description: 检测日志文件判断是否内存泄漏
    parameter：filename 检测的文件名
    '''
    def checkoom(self, filename):
#         nislog = open(filename, 'r')
        nislog = codecs.open(filename,'r','GB18030',errors='ignore')
        flag = False
        try:
            for line in nislog:
                if self.oompattern(line):
                    flag = True
                    break
        except Exception as e:
            self.logger.error("读取文件发生错误：%s，程序将结束！\n==================================" % e)
            sys.exit(-1)
        finally:
            nislog.close()
        
        os.chdir(self.basedir)
        hprof_filenames = glob.glob(self.hprof)
        slen = len(hprof_filenames)
        if slen > 0:
            flag = True
            
        if flag:
            self.logger.info("检测到内存溢出，ALARM!!!")
            self.stopservice(self.servicename)
            self.zip_dir(self.basedir, r"%s %s.zip" % (self.backupname, time.strftime('%Y-%m-%d %H-%M-%S', time.localtime(time.time()))))
            self.startservice(self.servicename)
            time.sleep(60)
            self._openurl()
        else:
            self.logger.info("没有检测到内存溢出，HAPPY!!!")
            self.checkservice(self.servicename)
    
    '''
    description:内存溢出监测模式
    parameter：parttens 所有模式，| 分割
    return True or False
    '''            
    def oompattern(self, s):
        p = re.compile(self.oompatterns)
        match = p.search(s)
        if match:
            return True
        return False
    
    '''
    description:停止服务
    parameter：name 服务名
    notice:注意net stop 和 sc stop的区别
    '''
    def stopservice(self, name):
        result = os.popen("sc query %s" % name).read()
        sceconds = 0;
        while "STOPPED" not in result:
            if "RUNNING" in result:
                self.logger.info("The service %s is running........" % name)
                try:
                    result = os.popen("sc stop %s" % name).read()
                except Exception as e:
                    self.logger.error("Stop service Failed：%s\n==================================" % e)
                    sys.exit(-1)
                self.logger.info("Stop service ........")
                time.sleep(5)
                sceconds += 5
            elif "START_PENDING" in result:
                self.logger.info("The service  %s is starting........" % name)
                time.sleep(2)
                sceconds += 2
            elif "STOP_PENDING" in result:
                self.logger.info("The service  %s is stopping........" % name)
                time.sleep(2)
                sceconds += 2
            elif "失败" in result:
                # 不能用self.is_process_exist()方法判断是否存在
                self.kill(self.imagename)
            else:
                self.logger.info("The service %s is in other status:%s" % (name, result))
                time.sleep(20)
                sceconds += 20  
            
            result = os.popen("sc query %s" % name).read()
            if  sceconds > int(self.breaktime):
                self.kill(self.imagename)
        self.logger.info("The service %s stop success." % name)  
        
    
    '''
    description:启动服务
    parameter：name 启动的服务名
    notice：服务必须已经停止，否则可能会存在问题
    '''
    def startservice(self, name):
        os.popen("sc start %s" % name).read()
        self.logger.info("The service %s is starting....... " % name)
        time.sleep(5)
        result = os.popen("sc query %s" % name).read()
        
        timecounting = 0 
        while "RUNNING" not in result:
            result = os.popen("sc query %s" % name).read()
            self.logger.info("The service %s is still starting......." % name)
            time.sleep(2)
            timecounting += 2
            if timecounting > 10:
                # 针对eclipse等ide开发工具占用nis服务的tomcat
                if self.is_process_exist("javaw.exe"):
                    self.kill("javaw.exe")
                    os.popen("sc start %s" % name).read()
                    
                    
            if timecounting > int(self.breaktime):
                self.logger.error("The service %s started failed:Timeout!\n==================================" % name)  
                sys.exit(-1)
                
            if "RUNNING" in result:
                time.sleep(10)
                result = os.popen("sc query %s" % name).read()
                
        self.logger.info("The service %s started success." % name)
    
    '''
    description:检测服务的状态，是否处于开启状态。
    '''
    def checkservice(self, name):
        self.logger.info("Is service %s in active?" % name)
        result = os.popen("sc query %s" % name).read()
        if "STOPPED" in result:
            self.logger.info("The service %s is stopped, trying to restore it." % name)
            os.popen("sc start %s" % name).read()
            tcount = 0
            while "RUNNING" not in result:
                result = os.popen("sc query %s" % name).read()
                self.logger.info("The service %s is still starting......." % name)
                time.sleep(2)
                tcount += 2
                
                if tcount > 10:
                    if self.is_process_exist("javaw.exe"):
                        self.kill("javaw.exe")
                        os.popen("sc start %s" % name).read()
                
                if tcount > int(self.breaktime):
                    self.logger.error("The service %s restore failed, please check the port:Timeout!\n==================================" % name)  
                    sys.exit(-1)
                
                if "RUNNING" in result:
                    time.sleep(5)
                    result = os.popen("sc query %s" % name).read()
                    
            self.logger.info("The service %s restore success." % name)
        else:
            self.logger.info("The service %s is in active." % name)
            
    '''
    description:主函数入口
    '''            
    def main(self):
        os.chdir(self.basedir)
        txt_filenames = glob.glob(self.std_log)
        slen = len(txt_filenames)
        if slen == 0:
            self.logger.info("不存在%s模式的文件\n==================================" % self.std_log)
            sys.exit(-1)
        fname = txt_filenames[slen - 1]
        self.logger.info("检测的文件为：%s" % fname)
        self.checkoom(r"%s\%s" % (self.basedir, fname))
    
    '''
    description:获取结径的结尾
    '''
    def path_leaf(self, path):
        head, tail = ntpath.split(path)
        return tail or ntpath.basename(head)
    
    '''
    description:进行nis系统版本的比较
    '''
    def compareTo(self, nis_v1, nis_v2):
        v2 = nis_v2.split(".")
        flag = len(nis_v1.split("."))
        if len(v2) < flag:
            for i in range(flag - len(v2)):
                nis_v2 = nis_v2 + ".0"
        elif len(v2) > flag:
            for i in range(len(v2) - flag):
                nis_v1 = nis_v1 + ".0"
        
        v2 = nis_v2.split(".")
        v1 = nis_v1.split(".")
        for i in range(len(v1)):
            if v1[i] > v2[i]:
                return 1
            elif v1[i] < v2[i]:
                return -1
        return 0
    
    '''
    description:解析orcus_web.xml,获得nis版本和nis程序访问地址
    '''
    def parse_orcus_web_xml(self, filepath):  
        soup = BeautifulSoup(open(filepath, encoding="UTF-8"))
        return soup.find("element", attrs={"key":"App.Version"})["value"], soup.find("element", attrs={"key":"web.context.url"})["value"]
    
    '''
    description:获得配置文件，不能用于日志配置，如要用于日志配置，请取消里面的日志打印程序，并将初始化配置前置在日志初始化之前
    '''
    def initconfig(self):
        config = configparser.ConfigParser()
        
        # self.logger.info("配置文件为：%s" % os.path.realpath(__file__))
        configfile = os.path.join(os.path.split(sys.path[0])[0], "oommonitor.config")
        
        config.read(configfile)
        if len(config.sections()) == 0:
            self.logger.warn("不使用配置文件，系统将自动初始化配置信息，默认监测服务名为：%s" % self.servicename)
        elif len(config.sections()) > 1:
            self.logger.error("配置文件配置错误！不允许多个配置项！CODE:%s\n==================================" % len(config.sections()))
            sys.exit(-1)
        else:
            self.logger.info("配置文件为：%s" % configfile)
            section = config.sections().pop()
            # 通过配置文件赋值
            for key in config[section]:
                if key in self.__dict__:
                    self.setattr(key, config[section][key])
        
        # 系统内部自动赋值
        result = os.popen("sc qc %s" % self.servicename).read()
        m = re.findall(r'([a-zA-Z]:(\\[\sA-Za-z0-9_\.-]*)+)', result)
        
        if len(self.imagename) == 0:
            self.imagename = self.path_leaf(m[0][0]).strip()
            
        TOMCAT_HOME = ntpath.dirname(ntpath.dirname(m[0][0]))
        
        if len(self.basedir) == 0:
            self.basedir = r"%s" % (TOMCAT_HOME + "\\logs")
            
        if len(self.backupname) == 0:
            self.backupname = r"%s" % (TOMCAT_HOME + "\\nis-logs")
        
        version_and_nisurl = self.parse_orcus_web_xml(TOMCAT_HOME + "\\webapps\\nis\\WEB-INF\\orcus_web.xml")
        self.nis_version = version_and_nisurl[0]
        if self.compareTo(self.nis_version, "12.7.0.3") > 0:
            if len(self.oomrunurl) == 0:
                self.oomrunurl = version_and_nisurl[1] + "oomrun"
                
#         self.logger.info(self.__dict__)
    
    '''
    description:设置类的属性
    '''            
    def setattr(self, name, value):
        self.__dict__[name] = value
        
        
    '''
    description:简单判断进程是否存在
    '''
    def is_process_exist(self, process_image):
        result = os.popen("tasklist /nh").read()
        if process_image in result.lower():
            return True
        return False
    
    '''
    description:杀死进程
    parameter：imagename 指定要终止的进程的映像名称，如cmd.exe。通配符 '*'可用来指定所有任务或映像名称。
    '''    
    def kill(self, imagename):
        self.logger.info("尝试强制杀死进程（进程映像为：%s）" % imagename)
        result = os.popen("TASKKILL /F /IM %s /T" % imagename).read()
        if "成功" in result:
            self.logger.info("强制杀死进程成功（进程映像为：%s）" % imagename)
        else:
            self.logger.info("强制杀死进程失败（进程映像为：%s）\n==================================" % imagename)
            sys.exit(-1)
            
    '''
    description:打开指定的url
    '''        
    def _openurl(self):
        if len(self.oomrunurl) > 2:
            try:
                self.logger.info("尝试请求重新加载nis系统数据...")
                urllib.request.urlopen(self.oomrunurl, None)
            except Exception as e:
                self.logger.error("打开url:%s失败：%s\n==================================" % (self.oomrunurl, e))
                sys.exit(-1)
            self.logger.info("请求重新加载nis系统数据成功!")
            
    
        
        
     
if __name__ == '__main__': 
    win32serviceutil.HandleCommandLine(OOMMonitor)
    
    

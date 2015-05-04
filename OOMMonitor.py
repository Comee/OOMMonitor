# -*- coding: utf-8 -*-
'''
Created on 2014年11月18日

@author: Leo
'''
import codecs
import configparser
import glob
import logging
import ntpath
import os
import re
import shutil
import sys
import time
import zipfile

import win32event
import win32service
import win32serviceutil

from logging import handlers
from urllib import request

from bs4 import BeautifulSoup


def unzip_file(zipfilename, unziptodir):
    '''
    description:解压zip文件
    parameter：zipfilename 压缩文件
    paramete：unziptodir压缩路径
    '''
    if not os.path.exists(unziptodir):
        os.mkdir(unziptodir, 0o777)
    zfobj = zipfile.ZipFile(zipfilename)
    for name in zfobj.namelist():
        name = name.replace('\\', '/')

        if name.endswith('/'):
            os.mkdir(os.path.join(unziptodir, name))
        else:
            ext_filename = os.path.join(unziptodir, name)
            ext_dir = os.path.dirname(ext_filename)
            if not os.path.exists(ext_dir):
                os.mkdir(ext_dir, 0o777)
            outfile = open(ext_filename, 'wb')
            outfile.write(zfobj.read(name))
            outfile.close()

def compare_to(nis_v1, nis_v2):
    '''
    description:进行nis系统版本的比较
    '''
    version_2 = nis_v2.split(".")
    flag = len(nis_v1.split("."))
    if len(version_2) < flag:
        for i in range(flag - len(version_2)):
            nis_v2 = nis_v2 + ".0"
    elif len(version_2) > flag:
        for i in range(len(version_2) - flag):
            nis_v1 = nis_v1 + ".0"

    version_2 = nis_v2.split(".")
    version_1 = nis_v1.split(".")
    for i in range(len(version_1)):
        if version_1[i] > version_2[i]:
            return 1
        elif version_1[i] < version_2[i]:
            return -1
    return 0

def path_leaf(path):
    '''
    description:获取路径的结尾
    '''
    head, tail = ntpath.split(path)
    return tail or ntpath.basename(head)

def parse_orcus_web_xml(filepath):
    '''
    description:解析orcus_web.xml,获得nis版本和nis程序访问地址
    '''
    soup = BeautifulSoup(open(filepath, encoding="UTF-8"))
    return (soup.find("element", attrs={"key":"App.Version"})["value"],
            soup.find("element", attrs={"key":"web.context.url"})["value"])

def is_process_exist(process_image):
    '''
    description:简单判断进程是否存在
    '''
    result = os.popen("tasklist /nh").read()
    if process_image in result.lower():
        return True
    return False

class OOMMonitor(win32serviceutil.ServiceFramework):
    '''
    Monitoring Java memory leak by java log file.

    python OOMMonitor.py install                 安装服务
    python OOMMonitor.py --startup auto install  让服务自动启动
    python OOMMonitor.py start                   启动服务
    python OOMMonitor.py restart                 重启服务
    python OOMMonitor.py stop                    停止服务
    python OOMMonitor.py remove                  删除/卸载服务
    '''
    # 服务名
    _svc_name_ = "OOMMonitor"
    # 服务显示名称
    _svc_display_name_ = "OOMMonitor"
    # 服务描述
    _svc_description_ = "Monitoring Java memory leak by java log file."
    # 日志换行符
    log_new_line = '\n==============================='
    # 日志输出的默认格式
    log_formatter = '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'


    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        # Create an event which we will use to wait on.
        # The "service stop" request will set this event.
        self.h_wait_stop = win32event.CreateEvent(None, 0, 0, None)
        self.is_alive = True

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
        self.imagename = "" #进程映像名
        self.oomrunurl = "" #重新加载数据的url

        # 日志配置 若为0 则只产生一个日志文件，且backup_count失效
        self.max_megabytes = 10
        self.backup_count = 5

        self.logger = self.get_logger()

    def SvcStop(self):
        '''
        @see: win32serviceutil.ServiceFramework#GetAcceptedControls
        '''
        # Before we do anything, tell the SCM we are starting the stop process.
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        # And set my event.
        win32event.SetEvent(self.h_wait_stop)

    def SvcDoRun(self):
        '''
        @see: win32serviceutil.ServiceFramework#SvcRun
        '''
        self.logger.info("Service core is running!")
        try:
            self.initconfig()
            self.main()
        except Exception as err:
            self.logger.error("Service core is dumped!%s%s", err, self.log_new_line)
            sys.exit(-1)
        else:
            self.logger.info("Service core is finished!%s", self.log_new_line)
            sys.exit(0)
        # We do nothing other than wait to be stopped!
        win32event.WaitForSingleObject(self.h_wait_stop, win32event.INFINITE)

    def get_logger(self):
        '''
        description:日志初始化
        '''
        logger = logging.getLogger('[OOMMonitor]')

        current_dir = os.path.join(os.path.split(sys.path[0])[0], "oommonitor.log")
        handler = handlers.RotatingFileHandler(current_dir,
                                               maxBytes=int(self.max_megabytes) * 1024 * 1024,
                                               backupCount=int(self.backup_count))
        formatter = logging.Formatter(self.log_formatter, '%Y-%m-%d %H:%M:%S')
        handler.setFormatter(formatter)

        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        return logger

    def zip_dir(self, dirname, zipfilename):
        '''
        description:压缩目录后并清空
        parameter：dirname  待压缩的目录
        paramete：zipfilename 压缩后文件名（包含路径）
        '''
        self.logger.info("待压缩目录为：%s  压缩后的文件名为：%s", dirname, zipfilename)
        if os.path.exists(dirname):
            filelist = []
            if os.path.isfile(dirname):
                filelist.append(dirname)
            else:
                for root, dirs, files in os.walk(dirname):  # @UnusedVariable dirs
                    for name in files:
                        filelist.append(os.path.join(root, name))

            zip_file = zipfile.ZipFile(zipfilename, "w", zipfile.zlib.DEFLATED)
            for tar in filelist:
                arcname = tar[len(dirname):]
                zip_file.write(tar, arcname)
            zip_file.close()
            self.logger.info("压缩完成，开始清空目录：%s", dirname)

            shutil.rmtree(dirname, True, None)
            self.logger.info("目录%s清空完成", dirname)
        else:
            self.logger.info("目录%s不存在", dirname)

    def checkoom(self, filename):
        '''
        description: 检测日志文件判断是否内存泄漏
        parameter：filename 检测的文件名
        '''
        # nislog = open(filename, 'r')
        nislog = codecs.open(filename, 'r', 'GB18030', errors='ignore')
        flag = False
        try:
            for line in nislog:
                if self.oompattern(line):
                    flag = True
                    break
        except Exception as err:
            self.logger.error("读取文件发生错误：%s，程序将结束！%s", err, self.log_new_line)
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
            backup_time = time.strftime('%Y-%m-%d %H-%M-%S', time.localtime(time.time()))
            self.zip_dir(self.basedir, r"%s %s.zip" % (self.backupname, backup_time))
            self.startservice(self.servicename)
            time.sleep(60)
            self._openurl()
        else:
            self.logger.info("没有检测到内存溢出，HAPPY!!!")
            self.checkservice(self.servicename)

    def oompattern(self, input_str):
        '''
        description:内存溢出监测模式
        parameter：parttens 所有模式，| 分割
        return True or False
        '''
        pattern = re.compile(self.oompatterns)
        match = pattern.search(input_str)
        if match:
            return True
        return False

    def stopservice(self, name):
        '''
        description:停止服务
        parameter：name 服务名
        notice:注意net stop 和 sc stop的区别
        '''
        result = os.popen("sc query %s" % name).read()
        sceconds = 0
        while "STOPPED" not in result:
            if "RUNNING" in result:
                self.logger.info("The service %s is running........", name)
                try:
                    result = os.popen("sc stop %s" % name).read()
                except Exception as err:
                    self.logger.error("Stop service Failed：%s%s", err, self.log_new_line)
                    sys.exit(-1)
                self.logger.info("Stop service ........")
                time.sleep(5)
                sceconds += 5
            elif "START_PENDING" in result:
                self.logger.info("The service  %s is starting........", name)
                time.sleep(2)
                sceconds += 2
            elif "STOP_PENDING" in result:
                self.logger.info("The service  %s is stopping........", name)
                time.sleep(2)
                sceconds += 2
            elif "失败" in result:
                # 不能用is_process_exist()方法判断是否存在
                self.kill(self.imagename)
            else:
                self.logger.info("The service %s is in other status:%s", name, result)
                time.sleep(20)
                sceconds += 20

            result = os.popen("sc query %s" % name).read()
            if  sceconds > int(self.breaktime):
                self.kill(self.imagename)
        self.logger.info("The service %s stop success.", name)

    def startservice(self, name):
        '''
        description:启动服务
        parameter：name 启动的服务名
        notice：服务必须已经停止，否则可能会存在问题
        '''
        os.popen("sc start %s" % name).read()
        self.logger.info("The service %s is starting....... ", name)
        time.sleep(5)
        result = os.popen("sc query %s" % name).read()

        timecounting = 0
        while "RUNNING" not in result:
            result = os.popen("sc query %s" % name).read()
            self.logger.info("The service %s is still starting.......", name)
            time.sleep(2)
            timecounting += 2
            if timecounting > 10:
                # 针对eclipse等ide开发工具占用nis服务的tomcat
                if is_process_exist("javaw.exe"):
                    self.kill("javaw.exe")
                    os.popen("sc start %s" % name).read()

            if timecounting > int(self.breaktime):
                self.logger.error(("The service %s started failed:",
                                   "Timeout!%s"), name, self.log_new_line)
                sys.exit(-1)

            if "RUNNING" in result:
                time.sleep(10)
                result = os.popen("sc query %s" % name).read()

        self.logger.info("The service %s started success.", name)

    def checkservice(self, name):
        '''
        description:检测服务的状态，是否处于开启状态。
        '''
        self.logger.info("Is service %s in active?", name)
        result = os.popen("sc query %s" % name).read()
        if "STOPPED" in result:
            self.logger.info("The service %s is stopped, trying to restore it.", name)
            os.popen("sc start %s" % name).read()
            tcount = 0
            while "RUNNING" not in result:
                result = os.popen("sc query %s" % name).read()
                self.logger.info("The service %s is still starting.......", name)
                time.sleep(2)
                tcount += 2

                if tcount > 10:
                    if is_process_exist("javaw.exe"):
                        self.kill("javaw.exe")
                        os.popen("sc start %s" % name).read()

                if tcount > int(self.breaktime):
                    self.logger.error(("The service %s restore failed, please check the port:",
                                       "Timeout!%s"), name, self.log_new_line)
                    sys.exit(-1)

                if "RUNNING" in result:
                    time.sleep(5)
                    result = os.popen("sc query %s" % name).read()

            self.logger.info("The service %s restore success.", name)
        else:
            self.logger.info("The service %s is in active.", name)

    def main(self):
        '''
        description:主函数入口
        '''
        os.chdir(self.basedir)
        txt_filenames = glob.glob(self.std_log)
        slen = len(txt_filenames)
        if slen == 0:
            self.logger.info("不存在%s模式的文件%s", self.std_log, self.log_new_line)
            sys.exit(-1)
        fname = txt_filenames[slen - 1]
        self.logger.info("检测的文件为：%s", fname)
        self.checkoom(r"%s\%s" % (self.basedir, fname))

    def initconfig(self):
        '''
        description:获得配置文件
        '''
        config = configparser.ConfigParser()

        # self.logger.info("配置文件为：%s" % os.path.realpath(__file__))
        configfile = os.path.join(os.path.split(sys.path[0])[0], "oommonitor.config")

        config.read(configfile)
        if len(config.sections()) == 0:
            self.logger.warn("不使用配置文件，系统将自动初始化配置信息，默认监测服务名为：%s", self.servicename)
        elif len(config.sections()) > 1:
            self.logger.error("配置文件配置错误！不允许多个配置项！CODE:%s%s",
                              len(config.sections()), self.log_new_line)
            sys.exit(-1)
        else:
            self.logger.info("配置文件为：%s", configfile)
            section = config.sections().pop()
            # 通过配置文件赋值
            for key in config[section]:
                if key in self.__dict__:
                    self.setattr(key, config[section][key])
            #日志的配置文件更新，从新获取logging
            self.logger = self.get_logger()

        # 系统内部自动赋值
        result = os.popen("sc qc %s" % self.servicename).read()
        binary_path_name = re.findall(r'([a-zA-Z]:(\\[\sA-Za-z0-9_\.-]*)+)', result)

        if len(self.imagename) == 0:
            self.imagename = path_leaf(binary_path_name[0][0]).strip()

        tomcat_home = ntpath.dirname(ntpath.dirname(binary_path_name[0][0]))

        if len(self.basedir) == 0:
            self.basedir = r"%s" % (tomcat_home + "\\logs")

        if len(self.backupname) == 0:
            self.backupname = r"%s" % (tomcat_home + "\\nis-logs")

        version_and_nisurl = parse_orcus_web_xml(tomcat_home +
                                                 "\\webapps\\nis\\WEB-INF\\orcus_web.xml")
        self.nis_version = version_and_nisurl[0]
        if compare_to(self.nis_version, "12.7.0.3") > 0:
            if len(self.oomrunurl) == 0:
                self.oomrunurl = version_and_nisurl[1] + "oomrun"

    def setattr(self, name, value):
        '''
        description:设置类的属性
        '''
        self.__dict__[name] = value

    def kill(self, imagename):
        '''
        description:杀死进程
        parameter：imagename 指定要终止的进程的映像名称，如cmd.exe。通配符 '*'可用来指定所有任务或映像名称。
        '''
        self.logger.info("尝试强制杀死进程（进程映像为：%s）", imagename)
        result = os.popen("TASKKILL /F /IM %s /T" % imagename).read()
        if "成功" in result:
            self.logger.info("强制杀死进程成功（进程映像为：%s）", imagename)
        else:
            self.logger.info("强制杀死进程失败（进程映像为：%s）%s", imagename, self.log_new_line)
            sys.exit(-1)

    def _openurl(self):
        '''
        description:打开指定的url
        '''
        if len(self.oomrunurl) > 2:
            try:
                self.logger.info("尝试请求重新加载nis系统数据...")
                request.urlopen(self.oomrunurl, None)
            except Exception as err:
                self.logger.error("打开url:%s失败：%s%s", self.oomrunurl, err, self.log_new_line)
                sys.exit(-1)
            self.logger.info("请求重新加载nis系统数据成功!")

if __name__ == '__main__':
    win32serviceutil.HandleCommandLine(OOMMonitor)

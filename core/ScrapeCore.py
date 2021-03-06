import core.DBConnector as DBConnector
import core.DataFetch as DataFetch
import core.DataParser as DataParser
import core.UserList as UserList
import core.EmailService as EmailService
import core.BloomFilter as BloomFilter
import time
import threading
import configparser
import logging
from core.Logger import log

# URL 组件
URL_PUBLIC = 'https://www.zhihu.com/people/'
URL_ANSWER = '/answers'
URL_FOLLOWING = '/following'
URL_FOLLOWER = '/followers'
URL_PAGE = '?page='

# 默认关注与被关注列表每页的大小
PAGE_SIZE = 20
# 爬虫动作时间间隔（单位：秒）
SCRAPE_TIME_INTERVAL = 2
# 正在关注页面时最大爬取页面范围(若为负数则代表不作限制)
FOLLOWING_PAGE_MAX = 200
# 关注着页面最大爬取页面范围(若为负数则代表不作限制)
FOLLOWER_PAGE_MAX = 100
# 是否分析正在关注列表
ANALYSE_FOLLOWING_LIST = True
# 是否分析关注者列表
ANALYSE_FOLLOWER_LIST = True

# 用户信息抓取线程数量
USER_INFO_SCRAPE_THREAD_NUM = 8
# 用户列表抓取线程数量
USER_LIST_SCRAPE_THREAD_NUM = 8

# 是否使用代理
IS_PROXY_ENABLE = True

# 是否使用邮件通知
IS_EMAIL_NOTIFICATION_ENABLE = False

# 爬虫起始 token
start_token = ''


# 用户信息抓取线程
class UserInfoScrapeThread(threading.Thread):
    def __init__(self, thread_name, data_fetch_module, user_token_cache_queue, cache_queue, bloom_filter):
        threading.Thread.__init__(self)
        self.thread_name = thread_name
        self.data_fetch_module = data_fetch_module
        self.user_token_cache_queue = user_token_cache_queue
        self.cache_queue = cache_queue
        self.bloom_filter = bloom_filter
        self.status = 'running'

    def run(self):
        if log.isEnabledFor(logging.DEBUG):
            log.debug('用户信息抓取线程' + self.thread_name + '启动')
        try:
            self.user_info_scrape()
        except Exception as e:
            if log.isEnabledFor(logging.ERROR):
                log.exception(e)
            self.status = 'error'

    # 爬取用户信息
    def user_info_scrape(self):

        # 为该线程绑定 session
        self.data_fetch_module.thread_bind_session(self.thread_name)

        if log.isEnabledFor(logging.DEBUG):
            log.debug('用户信息爬取线程[' + self.thread_name + ']开始运行')

        while True:
            # 从未分析 token 缓存列表中获取一个可用的token
            while True:
                token = self.user_token_cache_queue.get_token_from_cache_queue()
                if token is not None:
                    if self.is_token_available(token) is False:
                        break
                else:
                    time.sleep(0.5)

            # 抓取 token 对应用户的个人信息，并保存
            response = self.data_fetch_module.fetch_data_of_url(self.generate_user_info_url(token), self.thread_name)

            # 判断返回的数据是否有效，若有效再继续对数据进行分析
            if response is not None:
                if response == 'reuse':
                    # 将该 token 放回队列
                    self.user_token_cache_queue.add_token_into_cache_queue([token])
                else:
                    # 添加到待分析队列
                    self.cache_queue.add_data_into_user_info_cache_queue({DataParser.QUEUE_ELEM_HTML: response.text,
                                                                          DataParser.QUEUE_ELEM_TOKEN: token,
                                                                          DataParser.QUEUE_ELEM_THREAD_NAME: self.thread_name})

            # 爬取时间间隔
            time.sleep(SCRAPE_TIME_INTERVAL)

    # 判断 token 是否可用
    def is_token_available(self, token):
        # 使用布隆过滤器判断
        result = self.bloom_filter.exists(token)
        return result

    # 生成 token 对应用户的个人主页 URL
    @staticmethod
    def generate_user_info_url(token):
        return URL_PUBLIC + token + URL_ANSWER


# 用户关注列表抓取线程
class UserListScrapeThread(threading.Thread):
    def __init__(self, thread_name, db_connection, data_fetch_module, user_token_cache_queue, cache_queue):
        threading.Thread.__init__(self)
        self.thread_name = thread_name
        self.db_connection = db_connection
        self.data_fetch_module = data_fetch_module
        self.user_token_cache_queue = user_token_cache_queue
        self.cache_queue = cache_queue
        self.status = 'running'

    def run(self):
        if log.isEnabledFor(logging.DEBUG):
            log.debug('用户列表抓取线程' + self.thread_name + '启动')
        try:
            self.user_list_scrape()
        except Exception as e:
            if log.isEnabledFor(logging.ERROR):
                log.exception(e)
            self.status = 'error'

    # 爬取用户列表
    def user_list_scrape(self):

        # 为该线程绑定 session
        self.data_fetch_module.thread_bind_session(self.thread_name)

        if log.isEnabledFor(logging.DEBUG):
            log.debug('用户列表爬取线程[' + self.thread_name + ']开始运行')

        while True:
            # 从已分析 token 缓存列表中获取一个可用的token
            while True:
                token_info = self.user_token_cache_queue.get_token_form_analysed_cache_queue()
                if token_info is not None:
                    break
                time.sleep(0.5)

            # 分析正在关注列表
            if ANALYSE_FOLLOWING_LIST is True:
                # 计算页码范围
                following_page_size = 1
                if DataParser.USER_FOLLOWING_COUNT in token_info:
                    following_page_size = self.calculate_max_page(token_info[DataParser.USER_FOLLOWING_COUNT])
                if 0 < FOLLOWING_PAGE_MAX < following_page_size:
                    following_page_size = FOLLOWING_PAGE_MAX

                # 开始分析
                cur_page = 1
                while cur_page <= following_page_size:
                    # 获取数据
                    following_list_response = self.data_fetch_module.fetch_data_of_url(
                        self.generate_following_list_url(token_info[DataParser.USER_URL_TOKEN], cur_page),
                        self.thread_name)

                    # 判断返回的数据是否有效，若有效再对数据进行分析
                    if following_list_response is not None:
                        if following_list_response == 'reuse':
                            # 重新分析该页的列表
                            continue
                        else:
                            # 添加到分析队列
                            self.cache_queue.add_data_into_user_list_cache_queue({
                                DataParser.QUEUE_ELEM_HTML: following_list_response.text,
                                DataParser.QUEUE_ELEM_TOKEN: token_info[DataParser.USER_URL_TOKEN],
                                DataParser.QUEUE_ELEM_THREAD_NAME: self.thread_name})
                            cur_page += 1

                    time.sleep(SCRAPE_TIME_INTERVAL)

            # 分析关注者列表
            if ANALYSE_FOLLOWER_LIST is True:
                # 计算页码范围
                follower_page_size = 1
                if DataParser.USER_FOLLOWER_COUNT in token_info:
                    follower_page_size = self.calculate_max_page(token_info[DataParser.USER_FOLLOWER_COUNT])
                if follower_page_size > FOLLOWER_PAGE_MAX > 0:
                    follower_page_size = FOLLOWER_PAGE_MAX

                # 开始分析
                cur_page = 1
                while cur_page <= follower_page_size:
                    # 获取数据
                    follower_list_response = self.data_fetch_module.fetch_data_of_url(
                        self.generate_follower_list_url(token_info[DataParser.USER_URL_TOKEN], cur_page),
                        self.thread_name)

                    # 判断返回的数据是否有效，若有效再继续对数据进行分析
                    if follower_list_response is not None:
                        if follower_list_response == 'reuse':
                            # 重新分析该页的列表
                            continue
                        else:
                            # 添加到待分析队列
                            self.cache_queue.add_data_into_user_list_cache_queue({
                                DataParser.QUEUE_ELEM_HTML: follower_list_response.text,
                                DataParser.QUEUE_ELEM_TOKEN: token_info[DataParser.USER_URL_TOKEN],
                                DataParser.QUEUE_ELEM_THREAD_NAME: self.thread_name})
                            cur_page += 1

                    time.sleep(SCRAPE_TIME_INTERVAL)

    # 计算页码的范围最大值
    @staticmethod
    def calculate_max_page(total):
        if total % PAGE_SIZE == 0:
            return total // PAGE_SIZE
        else:
            return total // PAGE_SIZE + 1

    # 生成指定页码和 token 对应的正在关注列表 URL
    @staticmethod
    def generate_following_list_url(token, page):
        return URL_PUBLIC + token + URL_FOLLOWING + URL_PAGE + str(page)

    # 生成指定页码和 token 对应的关注者列表 URL
    @staticmethod
    def generate_follower_list_url(token, page):
        return URL_PUBLIC + token + URL_FOLLOWER + URL_PAGE + str(page)


class SpiderCore:
    def __init__(self):
        # 初始化配置参数
        self.config_init()

        # 初始化数据库模块
        self.DBConnectModule = DBConnector.DBConnectModule()
        # 初始化 BloomFilter 模块
        self.bloomFilterModule = BloomFilter.BloomFilter()
        # 初始化用户Token缓存
        self.userTokenCacheQueue = UserList.UserTokenCacheQueue(self.DBConnectModule)
        # 初始化待分析网页缓存
        self.cacheQueue = DataParser.CacheQueue()
        # 初始化数据获取模块
        self.dataFetchModule = DataFetch.DataFetchModule(IS_PROXY_ENABLE)
        # 初始化数据解析模块
        self.dataParseModule = DataParser.DataParseModule(self.DBConnectModule, self.userTokenCacheQueue,
                                                          self.cacheQueue, self.bloomFilterModule)
        # 初始化邮件服务模块
        if IS_EMAIL_NOTIFICATION_ENABLE is True:
            self.emailService = EmailService.EmailService(self.DBConnectModule)

        # 初始化用户信息爬取线程
        self.user_info_scrape_thread_list = []
        for thread_count in range(USER_INFO_SCRAPE_THREAD_NUM):
            thread_name = 'Info-Thread' + str(thread_count)
            user_info_scrape_thread = UserInfoScrapeThread(thread_name, self.dataFetchModule, self.userTokenCacheQueue,
                                                           self.cacheQueue, self.bloomFilterModule)
            self.user_info_scrape_thread_list.append(user_info_scrape_thread)

        # 初始化用户列表爬取线程
        self.user_list_scrape_thread_list = []
        for thread_count in range(USER_LIST_SCRAPE_THREAD_NUM):
            thread_name = 'List-Thread' + str(thread_count)
            user_list_scrape_thread = UserListScrapeThread(thread_name, self.DBConnectModule, self.dataFetchModule,
                                                           self.userTokenCacheQueue, self.cacheQueue)
            self.user_info_scrape_thread_list.append(user_list_scrape_thread)

        # 若有起始token则放入
        if start_token != '':
            self.userTokenCacheQueue.add_token_into_cache_queue([start_token])

        if log.isEnabledFor(logging.DEBUG):
            log.debug("爬虫核心模块初始化完毕")

    def start_spider(self):
        # 启动定时邮件线程
        if IS_EMAIL_NOTIFICATION_ENABLE is True:
            self.emailService.start_email_notification_service()

        # 启动数据解析线程
        self.dataParseModule.start_user_info_data_parse_thread()
        self.dataParseModule.start_user_list_data_parse_thread()

        # 启动用户信息爬取线程
        for user_info_scrape_thread in self.user_info_scrape_thread_list:
            user_info_scrape_thread.start()

        # 启动用户列表爬取线程
        for user_list_scrape_thread in self.user_list_scrape_thread_list:
            user_list_scrape_thread.start()

        if IS_EMAIL_NOTIFICATION_ENABLE is True:
            self.emailService.send_message("爬虫启动成功")

        # 工作线程检测并重启
        while True:
            # 检测邮件服务线程
            if IS_EMAIL_NOTIFICATION_ENABLE is True:
                if self.emailService.get_email_notification_service_status() == 'error':
                    self.emailService.restart_email_notification_service()
                    if log.isEnabledFor(logging.ERROR):
                        log.error('邮件服务线程重新启动')

            # 检测用户信息解析线程
            if self.dataParseModule.get_user_info_data_parse_thread_status() == 'error':
                self.dataParseModule.restart_user_info_data_parse_thread()
                if log.isEnabledFor(logging.ERROR):
                    log.error('用户信息解析线程重新启动')

            # 检测用户列表解析线程
            if self.dataParseModule.get_user_list_data_parse_thread_status() == 'error':
                self.dataParseModule.restart_user_list_data_parse_thread()
                if log.isEnabledFor(logging.ERROR):
                    log.error('用户信息解析线程重新启动')

            # 检测用户信息爬取线程
            for thread in self.user_info_scrape_thread_list:
                if thread.status == 'error':
                    thread_name = thread.thread_name
                    self.user_info_scrape_thread_list.remove(thread)
                    new_thread = UserInfoScrapeThread(thread_name, self.DBConnectModule, self.dataFetchModule,
                                                      self.userTokenCacheQueue, self.cacheQueue)
                    self.user_info_scrape_thread_list.append(new_thread)
                    new_thread.start()
                    if log.isEnabledFor(logging.ERROR):
                        log.error('用户信息爬取线程[' + thread_name + ']重新启动')

            # 检测用户列表爬取线程
            for thread in self.user_list_scrape_thread_list:
                if thread.status == 'error':
                    thread_name = thread.thread_name
                    self.user_list_scrape_thread_list.remove(thread)
                    new_thread = UserListScrapeThread(thread_name, self.DBConnectModule, self.dataFetchModule,
                                                      self.userTokenCacheQueue, self.cacheQueue)
                    self.user_list_scrape_thread_list.append(new_thread)
                    new_thread.start()
                    if log.isEnabledFor(logging.ERROR):
                        log.error('用户列表爬取线程[' + thread_name + ']重新启动')

            # 检测间隔
            time.sleep(180)

    @staticmethod
    def config_init():
        section = "spider_core"
        config = configparser.ConfigParser()
        config.read("core/spiderConfiguration.conf", encoding="utf8")

        # ScrapeCore配置
        global PAGE_SIZE
        global SCRAPE_TIME_INTERVAL
        global FOLLOWING_PAGE_MAX
        global FOLLOWER_PAGE_MAX
        global ANALYSE_FOLLOWING_LIST
        global ANALYSE_FOLLOWER_LIST
        global USER_INFO_SCRAPE_THREAD_NUM
        global USER_LIST_SCRAPE_THREAD_NUM
        global IS_PROXY_ENABLE
        global start_token
        global IS_EMAIL_NOTIFICATION_ENABLE
        PAGE_SIZE = int(config.get(section, "pageSize"))
        SCRAPE_TIME_INTERVAL = int(config.get(section, "scrapeTimeInterval"))
        FOLLOWING_PAGE_MAX = int(config.get(section, "followingPageMax"))
        FOLLOWER_PAGE_MAX = int(config.get(section, "followerPageMax"))
        ANALYSE_FOLLOWING_LIST = True if int(config.get(section, "analyseFollowingList")) == 1 else False
        ANALYSE_FOLLOWER_LIST = True if int(config.get(section, "analyse_FollowerList")) == 1 else False
        USER_INFO_SCRAPE_THREAD_NUM = int(config.get(section, "userInfoScrapeThreadNum"))
        USER_LIST_SCRAPE_THREAD_NUM = int(config.get(section, "userListScrapeThreadNum"))
        IS_PROXY_ENABLE = True if int(config.get(section, "isProxyEnable")) == 1 else False
        IS_EMAIL_NOTIFICATION_ENABLE = True if int(config.get(section, "isEmailNotificationEnable")) == 1 else False
        start_token = config.get(section, "startToken")

        # DataFetch配置
        DataFetch.PROXY_USAGE_MAX = int(config.get(section, "proxyUsageMax"))
        DataFetch.NETWORK_RECONNECT_TIMES = int(config.get(section, "networkReconnectTimes"))
        DataFetch.RESPONSE_ERROR_RETRY_TIME = int(config.get(section, "responseErrorRetryTimes"))
        DataFetch.CONNECT_TIMEOUT = int(config.get(section, "connectTimeout"))

        # UserList配置
        UserList.MAX_ANALYSED_CACHE_QUEUE_SIZE = int(config.get(section, "maxAnalysedCacheQueueSize"))
        UserList.REMAIN_ANALYSED_CACHE_QUEUE_SIZE = int(config.get(section, "remainAnalysedCacheQueueSize"))
        UserList.MAX_CACHE_QUEUE_SIZE = int(config.get(section, "maxCacheQueueSize"))
        UserList.REMAIN_CACHE_QUEUE_SIZE = int(config.get(section, "remainCacheQueueSize"))

        # 数据库配置
        DBConnector.DB_HOST = config.get(section, "dbHost")
        DBConnector.DB_USERNAME = config.get(section, "dbUsername")
        DBConnector.DB_PASSWORD = config.get(section, "dbPassword")
        DBConnector.DB_DATABASE = config.get(section, "dbDatabase")
        DBConnector.DB_CHARSET = config.get(section, "dbCharset")
        DBConnector.USER_INFO_BUFFER_SIZE = int(config.get(section, "user_info_buffer_size"))

        # 邮件通知配置
        EmailService.SMTP_SERVER_HOST = config.get(section, "smtpServerHost")
        EmailService.SMTP_SERVER_PORT = int(config.get(section, "smtpServerPort"))
        EmailService.SMTP_SERVER_PASSWORD = config.get(section, "smtpServerPassword")
        EmailService.SMTP_FROM_ADDR = config.get(section, "smtpFromAddr")
        EmailService.SMTP_TO_ADDR = config.get(section, "smtpToAddr")
        EmailService.SMTP_EMAIL_HEADER = config.get(section, "smtpEmailHeader")
        EmailService.SMTP_SEND_INTERVAL = int(config.get(section, "smtpSendInterval"))

# if __name__ == '__main__':
#     section = "spider_core"
#     config = configparser.ConfigParser()
#     config.read("spiderConfiguration.conf", encoding="utf8")
#     page_size = int(config.get(section, "pageSize"))
#     print(page_size)

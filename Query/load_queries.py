'''
Created on Apr 26, 2018

@author: riteshagarwal
'''
import random, os
import json
import logging.config
from threading import Thread
import threading
import time
from optparse import OptionParser
from Java_Connection import SDKClient
from com.couchbase.client.java.analytics import AnalyticsQuery, AnalyticsParams
from com.couchbase.client.java.query import N1qlQuery, N1qlParams, consistency
from java.lang import System, RuntimeException
from java.util.concurrent import TimeoutException, RejectedExecutionException,\
    TimeUnit
from com.couchbase.client.core import RequestCancelledException, CouchbaseException
import traceback, sys


HOTEL_DS_IDX_QUERY_TEMPLATES = [
    {"idx1" : "select meta().id from keyspacenameplaceholder where country is not null and `type` is not null "
              "and (any r in reviews satisfies r.ratings.`Check in / front desk` is not null end) limit 100 ",
    "idx2" : "select avg(price) as AvgPrice, min(price) as MinPrice, max(price) as MaxPrice from keyspacenameplaceholder "
              "where free_breakfast=True and free_parking=True and price is not null and array_count(public_likes)>5 "
              "and `type`='Hotel' group by country",
    "idx3" : "select city,country,count(*) from keyspacenameplaceholder where free_breakfast=True and free_parking=True "
              "group by country,city order by country,city limit 100 offset 100"}
]

def parse_options():
    parser = OptionParser()
    parser.add_option("-q", "--queries", dest="queries",
                      help="queries to be executed")
    
    parser.add_option("-s", "--server_ip", dest="server_ip",
                      help="server IP")
    
    parser.add_option("-p", "--port", dest="port",
                      help="query port")
    
    parser.add_option("-b", "--bucket", dest="bucket",
                      help="bucket name")
    
    parser.add_option("-l", "--log-level",
                      dest="loglevel", default="INFO", help="e.g -l debug,info,warning")

    parser.add_option("-n", "--querycount", dest="querycount", default= "10",
                      help="Number queries to be run to be always running on the cluster. For n1ql this param should = number of threads.")

    parser.add_option("-d", "--duration", dest="duration",
                      help="Duration for which queries to be run. WARNING: A duration of 0 will set queries to run infinitely (if n1ql flag is set)")

    parser.add_option("-t", "--threads", dest="threads", default="10",
                      help="Number of queries that will be run to completion")

    parser.add_option("-f", "--query_file", dest="query_file", default=None,
                      help="A file containing the list of queries you wish to be run")

    parser.add_option("-v", "--validate", dest="validate", default=False,
                      help="Set if you want n1ql queries to be validated or not")

    parser.add_option("-Q", "--n1ql", dest="n1ql", default=False,
                      help="Set if App is for query use")

    parser.add_option("-T", "--query_timeout", dest="query_timeout", default=300,
                      help="How long each query should run for")

    parser.add_option("-S", "--scan_consistency", dest="scan_consistency", default="NOT_BOUNDED",
                      help="The Scan_consistency of each query")

    parser.add_option("-c", "--collections_mode", dest="collections_mode", default=False,
                      help="Run the script for collections and auto-discover queries to be run")

    parser.add_option("-D", "--dataset", dest="dataset", default="hotel",
                      help="Dataset used in the test. Default = hotel")

    parser.add_option("-B", "--bucket_names",  dest="bucket_names", default="[]",
                      help="The list of bucket_names in the test running")

    parser.add_option("-P", "--print_duration",  dest="print_duration", default=3600,
                      help="The time interval you would like to wait before printing how many queries have been executed")

    (options, args) = parser.parse_args()
    
    return options

class query_load(SDKClient):
    
    def __init__(self, server_ip, server_port, queries, bucket, querycount, batch_size=50):
        self.ip = server_ip
        self.port = server_port
        self.queries = queries
        self.bucket = bucket
        
        self.failed_count = 0
        self.success_count = 0
        self.rejected_count = 0
        self.error_count = 0
        self.cancel_count = 0
        self.timeout_count = 0
        self.total_query_count = 0
        self.handles = []
        self.concurrent_batch_size = batch_size
        self.total_count = querycount
        
        SDKClient(self.ip, "Administrator", "password")
        self.connectionLive = False
        
        self.createConn(self.bucket, "Administrator", "password")
        
    def createConn(self, bucket, username=None, password=None):
        if username:
            self.username = username
        if password:
            self.password = password

        self.connectCluster(username, password)
        System.setProperty("com.couchbase.analyticsEnabled", "true");
        System.setProperty("com.couchbase.sentRequestQueueLimit", '1000');
        self.bucket = self.cluster.openBucket(bucket);
        self.connectionLive = True

    def closeConn(self):
        if self.connectionLive:
            try:
                self.bucket.close()
                self.disconnectCluster()
                self.connectionLive = False
            except CouchbaseException as e:
                time.sleep(10)
                try:
                    self.bucket.close()
                    time.sleep(5)
                except:
                    pass
                self.disconnectCluster()
                self.connectionLive = False
                log.error("%s"%e)
                traceback.print_exception(*sys.exc_info())
            except TimeoutException as e:
                time.sleep(10)
                try:
                    self.bucket.close()
                    time.sleep(5)
                except:
                    pass
                self.disconnectCluster()
                self.connectionLive = False
                log.error("%s"%e)
                traceback.print_exception(*sys.exc_info())
            except RuntimeException as e:
                log.info("RuntimeException from Java SDK. %s"%str(e))
                time.sleep(10)
                try:
                    self.bucket.close()
                    time.sleep(5)
                except:
                    pass
                self.disconnectCluster()
                self.connectionLive = False
                log.error("%s"%e)
                traceback.print_exception(*sys.exc_info())

    def _run_concurrent_queries(self, query, num_queries, duration = 120, n1ql_system_test=False, timeout=300, scan_consistency="NOT_BOUNDED",validate=False):
        # Run queries concurrently
        log.info("Running queries concurrently now...")
        threads = []
        total_query_count = 0
        query_count = 0
        if n1ql_system_test:
            name = threading.currentThread().getName()
            thread_name = "query_for_{0}".format(name)
            threads.append(Thread(target=self._run_query,
                                  name=thread_name,
                                  args=(random.choice(query), False, 0, True, timeout, scan_consistency,validate)))
        else:
            for i in range(0, num_queries):
                total_query_count += 1
                threads.append(Thread(target=self._run_query,
                                      name="query_thread_{0}".format(total_query_count), args=(random.choice(query),False)))
        i = 0
        for thread in threads:
            # Send requests in batches, and sleep for 5 seconds before sending another batch of queries.
            i += 1
            if i % self.concurrent_batch_size == 0:
                log.info("submitted {0} queries".format(i))
                time.sleep(5)
            thread.start()
            self.total_query_count += 1
            query_count += 1

        # For n1ql apps we want all queries to finish executing before starting up new queries
        if n1ql_system_test:
            for thread in threads:
                thread.join()

        st_time = time.time()
        i = 0
        if duration == 0:
            while True:
                if n1ql_system_test:
                    log.info("#" * 50)
                    self.total_count += 1
                    self._run_query(random.choice(query), False, 0, True, timeout, scan_consistency,validate)
                    i += 1
                    query_count += 1
                    self.total_query_count += 1
                else:
                    threads = []
                    log.info("#" * 50)
                    log.info("Total queries running on the cluster: %s" % self.total_count)
                    new_queries_to_run = num_queries - self.total_count
                    for i in range(0, new_queries_to_run):
                        total_query_count += 1
                        threads.append(Thread(target=self._run_query,
                                              name="query_thread_{0}".format(total_query_count),
                                              args=(random.choice(query), False)))
                        if total_query_count % 1000 == 0:
                            log.warning(
                                "%s queries submitted, %s failed, %s passed, %s rejected, %s cancelled, %s timeout" % (
                                    total_query_count, self.failed_count, self.success_count, self.rejected_count,
                                    self.cancel_count, self.timeout_count))
                    i = 0
                    self.total_count += new_queries_to_run
                    for thread in threads:
                        # Send requests in batches, and sleep for 5 seconds before sending another batch of queries.
                        i += 1
                        if i % self.concurrent_batch_size == 0:
                            log.info("submitted {0} queries".format(i))
                        #                    time.sleep(5)
                        thread.start()

                    time.sleep(2)
        else:
            while st_time+duration > time.time():
                if n1ql_system_test:
                    log.info("#"*50)
                    self.total_count += 1
                    self._run_query(random.choice(query), False, 0, True, timeout, scan_consistency, validate)
                    i += 1
                    query_count += 1
                    self.total_query_count += 1
                else:
                    threads = []
                    log.info("#"*50)
                    log.info ("Total queries running on the cluster: %s"%self.total_count)
                    new_queries_to_run = num_queries-self.total_count
                    for i in range(0, new_queries_to_run):
                        total_query_count += 1
                        threads.append(Thread(target=self._run_query,
                                              name="query_thread_{0}".format(total_query_count), args=(random.choice(query),False)))
                        if total_query_count%1000 == 0:
                            log.warning(
                        "%s queries submitted, %s failed, %s passed, %s rejected, %s cancelled, %s timeout" % (
                            total_query_count, self.failed_count, self.success_count, self.rejected_count, self.cancel_count, self.timeout_count))
                    i = 0
                    self.total_count += new_queries_to_run
                    for thread in threads:
                        # Send requests in batches, and sleep for 5 seconds before sending another batch of queries.
                        i += 1
                        if i % self.concurrent_batch_size == 0:
                            log.info("submitted {0} queries".format(i))
                        thread.start()
                    time.sleep(2)
        if n1ql_system_test:
            log.info("%s queries submitted" % query_count)
        else:
            log.info(
                "%s queries submitted, %s failed, %s passed, %s rejected, %s cancelled, %s timeout" % (
                    num_queries, self.failed_count, self.success_count, self.rejected_count, self.cancel_count, self.timeout_count))
        if self.failed_count+self.error_count != 0:
            raise Exception("Queries Failed:%s , Queries Error Out:%s"%(self.failed_count,self.error_count))
    
    def _run_query(self, query,validate_item_count=False, expected_count=0, n1ql_execution=False, timeout=300, scan_consistency="NOT_BOUNDED", validate=True):
        name = threading.currentThread().getName();
        client_context_id = name
        try:
            if n1ql_execution:
                if validate:
                    status, metrics, errors, results, handle = self.execute_statement_on_util(
                        query, timeout=timeout, client_context_id=client_context_id, thread_name=name, utility="n1ql",
                        scan_consistency=scan_consistency)
                    if status == "success":
                        split_query = query.split("WHERE")
                        primary_query = split_query[0] + "USE INDEX (`#primary`) WHERE" + split_query[1]
                        primary_status, primary_metrics, primary_errors, primary_results, primary_handle = self.execute_statement_on_util(
                        primary_query, timeout=timeout, client_context_id=client_context_id, thread_name=name, utility="n1ql",
                        scan_consistency=scan_consistency)

                else:
                    status, metrics, errors, results, handle = self.execute_statement_on_util(
                    query, timeout=timeout, client_context_id=client_context_id, thread_name=name, utility="n1ql", scan_consistency=scan_consistency)
            else:
                status, metrics, errors, results, handle = self.execute_statement_on_util(query, timeout=300,
                                                                                               client_context_id=client_context_id, thread_name=name)
            log.info("query : {0}".format(query))

            # Validate if the status of the request is success, and if the count matches num_items
            if status == "success":
                if validate_item_count:
                    if results[0]['$1'] != expected_count:
                        log.info("Query result : %s", results[0]['$1'])
                        log.info(
                            "********Thread %s : failure**********",
                            name)
                        self.failed_count += 1
                        self.total_count -= 1
                    else:
                        log.info(
                            "--------Thread %s : success----------",
                            name)
                        self.success_count += 1
                        self.total_count -= 1
                elif validate:
                    if primary_status == "success":
                        if metrics['resultCount'] != 0:
                            if results != primary_results:
                                log.info("Query result : %s", results[0]['$1'])
                                log.info(
                                    "********Thread %s : failure**********",
                                    name)
                                print "Mismatch of results!"
                                print("=" * 100)
                                print "Query: %s" % query
                                print "Primary Index Query: %s" % primary_query
                                print("=" * 100)
                                self.failed_count += 1
                                self.total_count -= 1
                            else:
                                log.info(
                                    "--------Thread %s : success----------",
                                    name)
                                self.success_count += 1
                                self.total_count -= 1
                        else:
                            print ("Results are zero! Please change query to have results!")
                            print query
                            self.failed_count += 1
                            self.total_count -= 1
                    else:
                        print "Primary Index did not run properly, cannot vaildate results"
                        self.failed_count += 1
                        self.total_count -= 1

                else:
                    log.info("--------Thread %s : success----------",
                                  name)
                    self.success_count += 1
                    self.total_count -= 1
            else:
                log.info("Status = %s", status)
                log.warning("query : {0}".format(query))
                log.warning("errors : {0}".format((str(errors))))
                log.warning("********Thread %s : failure**********", name)
                self.failed_count += 1
                self.total_count -= 1
        except Exception, e:
            log.info("********EXCEPTION: Thread %s **********", name)
            if str(e) == "Request Rejected":
                log.info("Error 503 : Request Rejected")
                self.rejected_count += 1
                self.total_count -= 1
            elif str(e) == "Request TimeoutException":
                log.info("Request TimeoutException")
                self.timeout_count += 1
                self.total_count -= 1
            elif str(e) == "Request RuntimeException":
                log.info("Request RuntimeException")
                self.timeout_count += 1
                self.total_count -= 1
            elif str(e) == "Request RequestCancelledException":
                log.info("Request RequestCancelledException")
                self.cancel_count += 1
                self.total_count -= 1
            elif str(e) == "CouchbaseException":
                log.info("General CouchbaseException")
                self.rejected_count += 1
                self.total_count -= 1
            elif str(e) == "Capacity cannot meet job requirement":
                log.info(
                    "Error 500 : Capacity cannot meet job requirement")
                self.rejected_count += 1
                self.total_count -= 1
            else:
                self.error_count +=1
                self.total_count -= 1
                log.info(str(e))
                
    def execute_statement_on_util(self, statement, timeout=300, client_context_id=None, username=None, password=None, analytics_timeout=300, thread_name=None, utility="cbas",scan_consistency="NOT_BOUNDED"):
        """
        Executes a statement on CBAS using the REST API using REST Client
        """
        pretty = "true"
        try:
            if utility == "n1ql":
                log.info("Running query on n1ql via %s: %s" % (thread_name, statement))
                response = self.execute_statement_on_n1ql(statement, pretty=True, client_context_id=client_context_id, username=username, password=password,
                                                          timeout=timeout,scan_consistency=scan_consistency)
            else:
                log.info("Running query on cbas via %s: %s"%(thread_name,statement))
                response = self.execute_statement_on_cbas(statement, pretty, client_context_id, username, password,timeout=timeout, analytics_timeout=analytics_timeout)
            
            if type(response) == str: 
                response = json.loads(response)
            if "errors" in response:
                errors = response["errors"]
                if type(errors) == str:
                    errors = json.loads(errors)
            else:
                errors = None
    
            if "results" in response:
                results = response["results"]
            else:
                results = None
    
            if "handle" in response:
                handle = response["handle"]
            else:
                handle = None
            
            if "metrics" in response:
                metrics = response["metrics"]
                if type(metrics) == str:
                    metrics = json.loads(metrics)
            else:
                metrics = None
            return response["status"], metrics, errors, results, handle
    
        except Exception,e:
            raise Exception(str(e))
        
    def execute_statement_on_cbas(self, statement, pretty=True, 
        client_context_id=None, 
        username=None, password=None, timeout = 300, analytics_timeout=300):

        params = AnalyticsParams.build()
        params = params.rawParam("pretty", pretty)
        params = params.rawParam("timeout", str(analytics_timeout)+"s")
        params = params.rawParam("username", username)
        params = params.rawParam("password", password)
        params = params.rawParam("clientContextID", client_context_id)
        if client_context_id:
            params = params.withContextId(client_context_id)
        
        output = {}
        q = AnalyticsQuery.simple(statement, params)
        try:
            result = self.bucket.query(q, 3600, TimeUnit.SECONDS)
            
            output["status"] = result.status()
            output["metrics"] = str(result.info().asJsonObject())
            
            try:
                output["results"] = str(result.allRows())
            except:
                output["results"] = None
                
            output["errors"] = json.loads(str(result.errors()))
            
            if str(output['status']) == "fatal":
                msg = output['errors'][0]['msg']
                if "Job requirement" in  msg and "exceeds capacity" in msg:
                    raise Exception("Capacity cannot meet job requirement")
            elif str(output['status']) == "success":
                output["errors"] = None
                pass
            else:
                log.info("analytics query %s failed status:{0},content:{1}".format(
                    output["status"], result))
                raise Exception("Analytics Service API failed")

        except TimeoutException as e:
            log.info("Request TimeoutException from Java SDK. %s"%str(e))
#             traceback.print_exception(*sys.exc_info())
            raise Exception("Request TimeoutException")
        except RequestCancelledException as e:
            log.info("RequestCancelledException from Java SDK. %s"%str(e))
#             traceback.print_exception(*sys.exc_info())
            raise Exception("Request RequestCancelledException")
        except RejectedExecutionException as e:
            log.info("Request RejectedExecutionException from Java SDK. %s"%str(e))
#             traceback.print_exception(*sys.exc_info())
            raise Exception("Request Rejected")
        except CouchbaseException as e:
            log.info("CouchbaseException from Java SDK. %s"%str(e))
#             traceback.print_exception(*sys.exc_info())
            raise Exception("CouchbaseException")
        except RuntimeException as e:
            log.info("RuntimeException from Java SDK. %s"%str(e))
#             traceback.print_exception(*sys.exc_info())
            raise Exception("Request RuntimeException")
        return output

    def execute_statement_on_n1ql(self, statement, pretty=True, client_context_id=None,
                                  username=None, password=None, timeout = 300,scan_consistency="NOT_BOUNDED"):
        params = N1qlParams.build()
        params = params.pretty(pretty)
        params = params.rawParam("timeout", str(timeout) + "s")
        if scan_consistency == "REQUEST_PLUS":
            params = params.consistency(consistency.ScanConsistency.REQUEST_PLUS)
        elif scan_consistency == "STATEMENT_PLUS":
            params = params.consistency(consistency.ScanConsistency.STATEMENT_PLUS)
        else:
            params = params.consistency(consistency.ScanConsistency.NOT_BOUNDED)

        if client_context_id:
            params = params.withContextId(client_context_id)

        output = {}
        q = N1qlQuery.simple(statement, params)
        try:
            result = self.bucket.query(q, 3600, TimeUnit.SECONDS)

            output["status"] = result.status()
            output["metrics"] = str(result.info().asJsonObject())

            try:
                output["results"] = result.allRows()
            except:
                output["results"] = None

            output["errors"] = json.loads(str(result.errors()))

            if str(output['status']) == "fatal":
                msg = output['errors'][0]['msg']
                if "Job requirement" in msg and "exceeds capacity" in msg:
                    raise Exception("Capacity cannot meet job requirement")
            elif str(output['status']) == "success":
                output["errors"] = None
                pass
            elif str(output['status'] == 'timeout'):
                log.info("timeout")
                raise Exception("Request TimeoutException")
            else:
                log.info("n1ql query %s failed status:{0},content:{1}".format(
                    output["status"], result))
                raise Exception("N1ql Service API failed")

        except TimeoutException as e:
            log.info("Request TimeoutException from Java SDK. %s" % str(e))
            raise Exception("Request TimeoutException")
        except RequestCancelledException as e:
            log.info("RequestCancelledException from Java SDK. %s" % str(e))
            raise Exception("Request RequestCancelledException")
        except RejectedExecutionException as e:
            log.info("Request RejectedExecutionException from Java SDK. %s" % str(e))
            raise Exception("Request Rejected")
        except CouchbaseException as e:
            log.info("CouchbaseException from Java SDK. %s" % str(e))
            raise Exception("CouchbaseException")
        except RuntimeException as e:
            log.info("RuntimeException from Java SDK. %s" % str(e))
            raise Exception("Request RuntimeException")
        return output

    def monitor_query_status(self, duration, print_duration=3600):
        st_time = time.time()
        update_time = time.time()
        if duration == 0:
            while True:
                if st_time + print_duration < time.time():
                    print "%s queries submitted, %s failed, %s passed, %s rejected, %s cancelled, %s timeout" % (
                        self.total_query_count, self.failed_count, self.success_count, self.rejected_count,
                        self.cancel_count, self.timeout_count)
                    st_time = time.time()
        else:
            while st_time + duration > time.time():
                if update_time + print_duration < time.time():
                    print "%s queries submitted, %s failed, %s passed, %s rejected, %s cancelled, %s timeout" % (
                        self.total_query_count, self.failed_count, self.success_count, self.rejected_count,
                        self.cancel_count, self.timeout_count)
                    update_time = time.time()

    def generate_queries_for_collections(self, dataset):

        idx_query_templates = HOTEL_DS_IDX_QUERY_TEMPLATES
        # This needs to be expanded when there are more datasets
        if dataset == "hotel" :
            idx_query_templates = HOTEL_DS_IDX_QUERY_TEMPLATES

        # Determine all scopes and collections for all buckets
        keyspaceListQuery = "select '`' || `namespace` || '`:`' || `bucket` || '`.`' || `scope` || '`.`' || `name` || '`' as `path` from system:all_keyspaces where `bucket` is not null;"
        queryResults = self.execute_statement_on_n1ql(keyspaceListQuery,True)

        keyspaceList = []
        for row in queryResults['results']:
            keyspaceList.append(json.loads(str(row))['path'])

        # For each collection, determine the indexes created

        queryList = []
        for keyspace in keyspaceList:
            idxListQuery = "select `name` from system:all_indexes where `using`='gsi' and " \
                           "'`' || `namespace_id` || '`:`' || `bucket_id` || '`.`' || `scope_id` || '`.`' || `keyspace_id` || '`' = '{0}' " \
                           "order by `bucket_id`, `scope_id`, `keyspace_id`, name".format(keyspace)

            queryResults = self.execute_statement_on_n1ql(idxListQuery, True)

            # For each index, select the corresponding query from the index-query mapping template for the dataset.
            # Add the query to the query_list after replacing the keyspace name

            if json.loads(queryResults['metrics'])['resultCount'] > 0:
                for row in queryResults['results']:
                    queryList.append(idx_query_templates[0][json.loads(str(row))["name"]].replace("keyspacenameplaceholder",keyspace))

        log.info("=====  Query List (total {0} queries )  ===== ".format(len(queryList)))
        for querystmt in queryList :
            log.info(querystmt)

        # Return query_list
        return queryList

    
def create_log_file(log_config_file_name, log_file_name, level):
    tmpl_log_file = open("jython.logging.conf")
    log_file = open(log_config_file_name, "w")
    log_file.truncate()
    for line in tmpl_log_file:
        newline = line.replace("@@LEVEL@@", level)
        newline = newline.replace("@@FILENAME@@", log_file_name.replace('\\', '/'))
        log_file.write(newline)
    log_file.close()
    tmpl_log_file.close()

def setup_log(options):
    abs_path = os.path.dirname(os.path.abspath(sys.argv[0]))
    str_time = time.strftime("%y-%b-%d_%H-%M-%S", time.localtime())
    root_log_dir = os.path.join(abs_path, "logs{0}queryrunner-{1}".format(os.sep, str_time))
    if not os.path.exists(root_log_dir):
        os.makedirs(root_log_dir)
    logs_folder = os.path.join(root_log_dir, "querylogs_%s" % time.time())
    os.mkdir(logs_folder)
    test_log_file = os.path.join(logs_folder, "test.log")
    log_config_filename = r'{0}'.format(os.path.join(logs_folder, "test.logging.conf"))
    create_log_file(log_config_filename, test_log_file, options.loglevel)
    logging.config.fileConfig(log_config_filename)
    print("Logs will be stored at {0}".format(logs_folder))

log = logging.getLogger()
options = None
def main():
    options = parse_options()
    setup_log(options)
    if options.n1ql:
        load = query_load(options.server_ip, options.port, [], options.bucket,int(options.threads), int(options.threads))
    else:
        load = query_load(options.server_ip, options.port, [], options.bucket,int(options.querycount))

    bucket_list = options.bucket_names.strip('[]').split(',')

    if options.collections_mode:
        queries = load.generate_queries_for_collections(options.dataset)
    else:
        if options.query_file:
            f = open(options.query_file, 'r')
            queries = f.readlines()
            i=0
            for query in queries:
                queries[i] = query.strip()
                for x in range(0, len(bucket_list)):
                    bucket_name = "bucket" + str(x)
                    if bucket_name in query:
                        queries[i] = query.replace(bucket_name, bucket_list[x])
                i+=1
            f.close()
        else:
            queries = ['SELECT name as id, result as bucketName, `type` as `Type`, array_length(profile.friends) as num_friends FROM  ds1 where duration between 3009 and 3010 and profile is not missing and array_length(profile.friends) > 5 limit 100',
                'SELECT name as id, result as bucketName, `type` as `Type`, array_length(profile.friends) as num_friends FROM  ds2 where duration between 3009 and 3010 and profile is not missing',
                 'select sum(friends.num_friends) from (select array_length(profile.friends) as num_friends from ds3) as friends',
                 'SELECT name as id, result as Result, `type` as `Type`, array_length(profile.friends) as num_friends FROM  ds4 where result = "SUCCESS" and profile is not missing and array_length(profile.friends) = 5 and duration between 3009 and 3010 UNION ALL SELECT name as id, result as Result, `type` as `Type`, array_length(profile.friends) as num_friends FROM  ds4 where result != "SUCCESS" and profile is not missing and array_length(profile.friends) = 5 and duration between 3010 and 3012']
    if options.n1ql:
        threads = []
        for i in range(0, load.concurrent_batch_size):
            threads.append(Thread(target=load._run_concurrent_queries,
                                  name="query_thread_{0}".format(i),
                                  args=(queries, int(options.querycount), int(options.duration), options.n1ql, options.query_timeout, options.scan_consistency,options.validate)))

        threads.append(Thread(target=load.monitor_query_status, name="monitor_thread", args=(int(options.duration),int(options.print_duration))))

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()
    else:
        load._run_concurrent_queries(queries, int(options.querycount), duration=int(options.duration))

    print("%s queries submitted, %s failed, %s passed, %s rejected, %s cancelled, %s timeout" % (
        load.total_query_count, load.failed_count, load.success_count, load.rejected_count, load.cancel_count, load.timeout_count))

    print(load.total_count)
    print("Done!!")

'''
    /opt/jython/bin/jython -J-cp '../Couchbase-Java-Client-2.5.6/*' load_queries.py --server_ip 172.23.108.231 --port 8095 --duration 600 --bucket default --querycount 10
'''
if __name__ == "__main__":
    main()

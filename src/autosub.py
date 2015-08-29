########################################################################
# autosub.py -- the entry point to autosub, initializes queues, starts
#       threads, and cleans up if autosub is stopped using SIGUSR2.
#
# Copyright (C) 2015 Andreas Platschek <andi.platschek@gmail.com>
#                    Martin  Mosbeck   <martin.mosbeck@gmx.at>
# License GPL V2 or later (see http://www.gnu.org/licenses/gpl2.txt)
########################################################################

import threading, queue
import email, getpass, imaplib, os, time
import sqlite3 as lite
import fetcher, worker, sender, logger, generator, activator
import optparse
import signal
import logging
import configparser
import common as c

def sig_handler(signum, frame):
   logger_queue.put(dict({"msg": "Shutting down autosub...", "type": "INFO", "loggername": "Main"}))
   exit_flag = 1

########################################

####
#   check_and_init_db_table
####
def check_and_init_db_table(cur, con, tablename, fields):
   sqlcmd = "SELECT name FROM sqlite_master WHERE type == 'table' AND name = '" + tablename + "';"
   cur.execute(sqlcmd)
   res = cur.fetchall()
   if res:
      logmsg = 'table ' + tablename + ' exists'
      c.log_a_msg(logger_queue, "autosub.py", logmsg, "DEBUG")
      #TODO: in this case, we might want to check if one entry per task is already there, and add new
      #      empty entries in case a task does not have one. This is only a problem, if the number of
      #      tasks in the config file is changed AFTER the TaskStats table has been changed!
      return 0
   else:
      logmsg = 'table ' + tablename + ' does not exist'
      c.log_a_msg(logger_queue, "autosub.py", logmsg, "DEBUG")

      sqlcmd = "CREATE TABLE " + tablename + "(" + fields + ");"
      cur.execute(sqlcmd)
      con.commit()
      return 1

####
# init_deb_statvalue()
#
# Add entries for the statistics counters, and initialize them to 0.
####
def init_db_statvalue(cur, con, countername, value):
      sql_cmd="INSERT INTO StatCounters (CounterId, Name, Value) VALUES(NULL, '" + countername + "', " + str(value) + ");"
      cur.execute(sql_cmd);
      con.commit();
####
# set_general_config_param()
####
def set_general_config_param(cur, con, configitem, content):
     sql_cmd="INSERT INTO GeneralConfig (ConfigItem, Content) VALUES('" + configitem + "', '" + content + "');"
     cur.execute(sql_cmd);
     con.commit();

####
# load_specialmessage_to_db()
####
def load_specialmessage_to_db(cur, con, msgname, filename):
     with open (filename, "r") as smfp:
        data=smfp.read()
     smfp.close()
     sql_cmd="INSERT INTO SpecialMessages (EventName, EventText) VALUES('" + msgname + "', '" + data + "');"
     cur.execute(sql_cmd)
     con.commit()

####
# Check if all databases, tables, etc. are available, or if they have to be created.
# if non-existent --> create them
####
def init_ressources(numThreads, numTasks, coursedb, semesterdb):
   cur,con = c.connect_to_db(semesterdb, logger_queue, "autosub.py") 

   ####################
   ####### Users ######
   ####################
   check_and_init_db_table(cur, con, "Users", "UserId INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT, Email TEXT, FirstMail DATETIME, LastDone DATETIME, CurrentTask INT")
   ####################
   ##### TaskStats ####
   ####################
   ret = check_and_init_db_table(cur, con, "TaskStats", "TaskId INTEGER PRIMARY KEY, NrSubmissions INT, NrSuccessful INT")
   if ret:
      numTasks = numTasks
      for t in range (1, numTasks+1):
         sql_cmd="INSERT INTO TaskStats (TaskId, NrSubmissions, NrSuccessful) VALUES("+ str(t) + ", 0, 0);"
         cur.execute(sql_cmd);
      con.commit();
   ####################
   ### StatCounters ###
   ####################
   ret = check_and_init_db_table(cur, con, "StatCounters", "CounterId INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT, Value INT")
   if ret:
      # add the stat counter entries and initialize them to 0:
      init_db_statvalue(cur, con, 'nr_mails_fetched', 0)
      init_db_statvalue(cur, con, 'nr_mails_sent', 0)
      init_db_statvalue(cur, con, 'nr_questions_received', 0)
      init_db_statvalue(cur, con, 'nr_non_registered', 0)
      init_db_statvalue(cur, con, 'nr_status_requests', 0)

   ####################
   ##### UserTasks ####
   ####################
   ret = check_and_init_db_table(cur, con, "UserTasks", "UniqueId INTEGER PRIMARY KEY AUTOINCREMENT, TaskNr INT, UserId INT, TaskParameters TEXT, TaskDescription TEXT, TaskAttachments TEXT, NrSubmissions INTEGER, FirstSuccessful INTEGER")

   ####################
   #### Whitelist #####
   ####################
   ret = check_and_init_db_table(cur, con, "Whitelist", "UniqueId INTEGER PRIMARY KEY AUTOINCREMENT, Email TEXT")

   ####################
   # Directory users ##
   ####################
   c.check_dir_mkdir("users", logger_queue, "autosub.py")
   con.close() # close here, since we re-open the databse in the while(True) loop


   cur,con = c.connect_to_db(coursedb, logger_queue, "autosub.py")

   ####################
   ## SpecialMessages #
   ####################
   ret = check_and_init_db_table(cur, con, "SpecialMessages", "EventName TEXT PRIMARY KEY, EventText TEXT")
   if ret: # that table did not exists, therefore we use the .txt files to initialize it!
      load_specialmessage_to_db(cur, con, 'WELCOME', 'SpecialMessages/welcome.txt')
      load_specialmessage_to_db(cur, con, 'USAGE', 'SpecialMessages/usage.txt')
      load_specialmessage_to_db(cur, con, 'QUESTION', 'SpecialMessages/question.txt')
      load_specialmessage_to_db(cur, con, 'INVALID', 'SpecialMessages/invalidtask.txt')
      load_specialmessage_to_db(cur, con, 'CONGRATS', 'SpecialMessages/congratulations.txt')
      load_specialmessage_to_db(cur, con, 'REGOVER', 'SpecialMessages/registrationover.txt')
      load_specialmessage_to_db(cur, con, 'NOTALLOWED', 'SpecialMessages/notallowed.txt')
      load_specialmessage_to_db(cur, con, 'CURLAST', 'SpecialMessages/curlast.txt')
      load_specialmessage_to_db(cur, con, 'DEADTASK', 'SpecialMessages/deadtask.txt')
   #####################
   # TaskConfiguration #
   #####################
   ret = check_and_init_db_table(cur, con, "TaskConfiguration", "TaskNr INT PRIMARY KEY, TaskStart DATETIME, TaskDeadline DATETIME, PathToTask TEXT, GeneratorExecutable TEXT, TestExecutable TEXT, Score INT, TaskOperator TEXT, TaskActive BOOLEAN")
   ####################
   ### GeneralConfig ##
   ####################
   ret = check_and_init_db_table(cur, con, "GeneralConfig", "ConfigItem Text PRIMARY KEY, Content TEXT")
   
   #####################
   # Num workers,tasks #
   #####################
   if ret: # if that table did not exist, load the defaults given in the configuration file
      set_general_config_param(cur, con, 'num_workers', str(numThreads))
      set_general_config_param(cur, con, 'num_tasks', str(numTasks))
      set_general_config_param(cur, con, 'registration_deadline', 'NULL')
      set_general_config_param(cur, con, 'archive_dir','archive/')
      set_general_config_param(cur, con, 'admin_email','')

   con.close()

########################################
if __name__ == '__main__':

   threadID = 1
   worker_t = []
   exit_flag = 0

   parser = optparse.OptionParser()
   parser.add_option("-c", "--config-file", dest="configfile", type="string",
              help=("The config file used for this instance of autosub."))

   parser.set_defaults(configfile="default.cfg")
   opts, args = parser.parse_args()

   config = configparser.ConfigParser()
   config.readfp(open(opts.configfile))
   imapserver = config.get('imapserver', 'servername')
   autosub_user = config.get('imapserver', 'username')
   autosub_passwd = config.get('imapserver', 'password')
   autosub_mail = config.get('imapserver', 'email')
   smtpserver = config.get('smtpserver', 'servername')
   numThreads = config.getint('general', 'num_workers')
   queueSize = config.getint('general', 'queue_size')

   try:
      poll_period = config.getint('general', 'poll_period')
   except:
      poll_period = 60

   try:
      semesterdb = config.get('general', 'semesterdb')
   except:
      semesterdb = 'semester.db'

   try:
      coursedb = config.get('general', 'coursedb')
   except:
      coursedb = 'course.db'

   numTasks = config.getint('challenge','num_tasks')

   job_queue = queue.Queue(queueSize)
   sender_queue = queue.Queue(queueSize)
   logger_queue = queue.Queue(queueSize)
   gen_queue = queue.Queue(queueSize)

   #Before we do anything else: start the logger thread, so we can log whats going on
   logger_t = logger.autosubLogger(threadID, "logger", logger_queue)#, logging.DEBUG)
   logger_t.daemon = True # make the logger thread a daemon, this way the main
                       # will clean it up before terminating!
   logger_t.start()
   threadID += 1

   signal.signal(signal.SIGUSR1, sig_handler)

   init_ressources(numThreads,numTasks, coursedb, semesterdb)

   sender_t = sender.mailSender(threadID, "sender", sender_queue, autosub_mail, autosub_user, autosub_passwd, smtpserver, logger_queue, coursedb, semesterdb)
   sender_t.daemon = True # make the sender thread a daemon, this way the main
                          # will clean it up before terminating!
   sender_t.start()
   threadID += 1

   fetcher_t = fetcher.mailFetcher(threadID, "fetcher", job_queue, sender_queue, gen_queue, autosub_user, autosub_passwd, imapserver, logger_queue, poll_period, coursedb, semesterdb)
   fetcher_t.daemon = True # make the fetcher thread a daemon, this way the main
                        # will clean it up before terminating!
   fetcher_t.start()
   threadID += 1

   generator_t = generator.taskGenerator(threadID, "generator", gen_queue, sender_queue, logger_queue, coursedb)
   generator_t.daemon = True # make the fetcher thread a daemon, this way the main
                             # will clean it up before terminating!
   generator_t.start()
   threadID += 1

   activator_t = activator.taskActivator(threadID, "activator", gen_queue, sender_queue, logger_queue, coursedb, semesterdb)
   activator_t.daemon = True # make the fetcher thread a daemon, this way the main
                             # will clean it up before terminating!
   activator_t.start()
   threadID += 1



   msg_config = "Used config-file: " + opts.configfile
   logger_queue.put(dict({"msg": msg_config, "type": "INFO", "loggername": "Main"}))
   #Next we start a couple of worker threads:

   while (threadID <= numThreads + 3):
      tName = "Worker" + str(threadID-3)
      t = worker.worker(threadID, tName, job_queue, gen_queue, sender_queue, logger_queue, coursedb, semesterdb)
      t.daemon = True
      t.start()
      worker_t.append(t)
      threadID += 1

      logger_queue.put(dict({"msg": "All threads started successfully", "type": "INFO", "loggername": "Main"}))

   while (not exit_flag):
      time.sleep(100)

   time.sleep(1) # give the logger thread a little time write the last log message 


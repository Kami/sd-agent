'''
	Server Density
	www.serverdensity.com
	----
	A web based server resource monitoring application

	Licensed under Simplified BSD License (see LICENSE)
	(C) Boxed Ice 2009 all rights reserved
'''

# SO references
# http://stackoverflow.com/questions/446209/possible-values-from-sys-platform/446210#446210
# http://stackoverflow.com/questions/682446/splitting-out-the-output-of-ps-using-python/682464#682464
# http://stackoverflow.com/questions/1052589/how-can-i-parse-the-output-of-proc-net-dev-into-keyvalue-pairs-per-interface-us

# Core modules
import httplib # Used only for handling httplib.HTTPException (case #26701)
import logging
import logging.handlers
import md5 # I know this is depreciated, but we still support Python 2.4 and hashlib is only in 2.5. Case 26918
import platform
import re
import subprocess
import sys
import urllib
import urllib2

# We need to return the data using JSON. As of Python 2.6+, there is a core JSON
# module. We have a 2.4/2.5 compatible lib included with the agent but if we're
# on 2.6 or above, we should use the core module which will be faster
pythonVersion = platform.python_version_tuple()

if int(pythonVersion[1]) >= 6: # Don't bother checking major version since we only support v2 anyway
	import json
else:
	import minjson

class checks:
	
	def __init__(self, agentConfig):
		self.agentConfig = agentConfig
		self.mysqlConnectionsStore = None
		self.mysqlCreatedTmpDiskTablesStore = None
		self.mysqlSlowQueriesStore = None
		self.mysqlTableLocksWaited = None
		self.networkTrafficStore = {}
		self.nginxRequestsStore = None
		
	def getApacheStatus(self):
		self.checksLogger.debug('getApacheStatus: start')
		
		if self.agentConfig['apacheStatusUrl'] != 'http://www.example.com/server-status/?auto':	# Don't do it if the status URL hasn't been provided
			self.checksLogger.debug('getApacheStatus: config set')
			
			try: 
				self.checksLogger.debug('getApacheStatus: attempting urlopen')
				
				request = urllib2.urlopen(self.agentConfig['apacheStatusUrl'])
				response = request.read()
				
			except urllib2.HTTPError, e:
				self.checksLogger.error('Unable to get Apache status - HTTPError = ' + str(e))
				return False
				
			except urllib2.URLError, e:
				self.checksLogger.error('Unable to get Apache status - URLError = ' + str(e))
				return False
				
			except httplib.HTTPException, e:
				self.checksLogger.error('Unable to get Apache status - HTTPException = ' + str(e))
				return False
				
			except Exception, e:
				import traceback
				self.checksLogger.error('Unable to get Apache status - Exception = ' + traceback.format_exc())
				return False
				
			self.checksLogger.debug('getApacheStatus: urlopen success, start parsing')
			
			# Split out each line
			lines = response.split('\n')
			
			# Loop over each line and get the values
			apacheStatus = {}
			
			self.checksLogger.debug('getApacheStatus: parsing, loop')
			
			# Loop through and extract the numerical values
			for line in lines:
				values = line.split(': ')
				
				try:
					apacheStatus[str(values[0])] = values[1]
					
				except IndexError:
					break
			
			self.checksLogger.debug('getApacheStatus: parsed')
			
			try:
				if apacheStatus['ReqPerSec'] != False and apacheStatus['BusyWorkers'] != False and apacheStatus['IdleWorkers'] != False:
					self.checksLogger.debug('getApacheStatus: completed, returning')
					
					return {'reqPerSec': apacheStatus['ReqPerSec'], 'busyWorkers': apacheStatus['BusyWorkers'], 'idleWorkers': apacheStatus['IdleWorkers']}
				
				else:
					self.checksLogger.debug('getApacheStatus: completed, status not available')
					
					return False
				
			# Stops the agent crashing if one of the apacheStatus elements isn't set (e.g. ExtendedStatus Off)	
			except IndexError:
				self.checksLogger.debug('getApacheStatus: IndexError - ReqPerSec, BusyWorkers or IdleWorkers not present')
				
			except KeyError:
				self.checksLogger.debug('getApacheStatus: IndexError - KeyError, BusyWorkers or IdleWorkers not present')
								
				return False
			
		else:
			self.checksLogger.debug('getApacheStatus: config not set')
			
			return False
		
	def getDiskUsage(self):
		self.checksLogger.debug('getDiskUsage: start')
		
		# Memory logging (case 27152)
		if self.agentConfig['debugMode'] and sys.platform == 'linux2':
			mem = subprocess.Popen(['free', '-m'], stdout=subprocess.PIPE, close_fds=True).communicate()[0]
			self.checksLogger.debug('getDiskUsage: memory before Popen - ' + str(mem))
		
		# Get output from df
		try:
			self.checksLogger.debug('getDiskUsage: attempting Popen')
			
			df = subprocess.Popen(['df', '-k'], stdout=subprocess.PIPE, close_fds=True).communicate()[0] # -k option uses 1024 byte blocks so we can calculate into MB
			
		except Exception, e:
			import traceback
			self.checksLogger.error('getDiskUsage: exception = ' + traceback.format_exc())
			return False
		
		# Memory logging (case 27152)
		if self.agentConfig['debugMode'] and sys.platform == 'linux2':
			mem = subprocess.Popen(['free', '-m'], stdout=subprocess.PIPE, close_fds=True).communicate()[0]
			self.checksLogger.debug('getDiskUsage: memory after Popen - ' + str(mem))
		
		self.checksLogger.debug('getDiskUsage: Popen success, start parsing')
			
		# Split out each volume
		volumes = df.split('\n')
		
		self.checksLogger.debug('getDiskUsage: parsing, split')
		
		# Remove first (headings) and last (blank)
		volumes.pop(0)
		volumes.pop()
		
		self.checksLogger.debug('getDiskUsage: parsing, pop')
		
		usageData = []
		
		regexp = re.compile(r'([0-9]+)')
		
		previous_volume = ''
		
		self.checksLogger.debug('getDiskUsage: parsing, start loop')

		for volume in volumes:
			volume = (previous_volume + volume).split(None, 10)
			
			# Handle df output wrapping onto multiple lines (case 27078)
			# Thanks to http://github.com/sneeu
			if len(volume) == 1:
				previous_volume = volume[0]
				continue
			else:
				previous_volume = ''
			
			# Sometimes the first column will have a space, which is usually a system line that isn't relevant
			# e.g. map -hosts              0         0          0   100%    /net
			# so we just get rid of it
			if re.match(regexp, volume[1]) == None:
				
				pass
				
			else:			
				try:
					volume[2] = int(volume[2]) / 1024 / 1024 # Used
					volume[3] = int(volume[3]) / 1024 / 1024 # Available
				except IndexError:
					self.checksLogger.debug('getDiskUsage: parsing, loop IndexError - Used or Available not present')
					
				except KeyError:
					self.checksLogger.debug('getDiskUsage: parsing, loop KeyError - Used or Available not present')
				
				usageData.append(volume)
		
		self.checksLogger.debug('getDiskUsage: completed, returning')
			
		return usageData
	
	def getLoadAvrgs(self):
		self.checksLogger.debug('getLoadAvrgs: start')
		
		# If Linux like procfs system is present and mounted we use loadavg, else we use uptime
		if sys.platform == 'linux2' or (sys.platform.find('freebsd') != -1 and self.linuxProcFsLocation != False):
			self.checksLogger.debug('getLoadAvrgs: %s' % ('linux2' if sys.platform == 'linux2' else 'freebsd (loadavg)'))
			
			try:
				self.checksLogger.debug('getLoadAvrgs: attempting open')
				
				if sys.platform == 'linux2':
					loadAvrgProc = open('/proc/loadavg', 'r')
				else:
					loadAvrgProc = open(self.linuxProcFsLocation + '/loadavg', 'r')
					
				uptime = loadAvrgProc.readlines()
				
			except IOError, e:
				self.checksLogger.error('getLoadAvrgs: exception = ' + str(e))
				return False
			
			self.checksLogger.debug('getLoadAvrgs: open success')
				
			loadAvrgProc.close()
			
			uptime = uptime[0] # readlines() provides a list but we want a string
		
		elif sys.platform.find('freebsd') != -1 and self.linuxProcFsLocation == False:
			self.checksLogger.debug('getLoadAvrgs: freebsd (uptime)')
			
			try:
				self.checksLogger.debug('getLoadAvrgs: attempting Popen')
				
				uptime = subprocess.Popen(['uptime'], stdout=subprocess.PIPE, close_fds=True).communicate()[0]
				
			except Exception, e:
				import traceback
				self.checksLogger.error('getLoadAvrgs: exception = ' + traceback.format_exc())
				return False
				
			self.checksLogger.debug('getLoadAvrgs: Popen success')
			
		elif sys.platform == 'darwin':
			self.checksLogger.debug('getLoadAvrgs: darwin')
			
			# Get output from uptime
			try:
				self.checksLogger.debug('getLoadAvrgs: attempting Popen')
				
				uptime = subprocess.Popen(['uptime'], stdout=subprocess.PIPE, close_fds=True).communicate()[0]
				
			except Exception, e:
				import traceback
				self.checksLogger.error('getLoadAvrgs: exception = ' + traceback.format_exc())
				return False
				
			self.checksLogger.debug('getLoadAvrgs: Popen success')
		
		self.checksLogger.debug('getLoadAvrgs: parsing')
				
		# Split out the 3 load average values
		loadAvrgs = re.findall(r'([0-9]+\.\d+)', uptime)
		loadAvrgs = {'1': loadAvrgs[0], '5': loadAvrgs[1], '15': loadAvrgs[2]}	
	
		self.checksLogger.debug('getLoadAvrgs: completed, returning')
	
		return loadAvrgs
		
	def getMemoryUsage(self):
		self.checksLogger.debug('getMemoryUsage: start')
		
		# If Linux like procfs system is present and mounted we use meminfo, else we use "native" mode (vmstat and swapinfo)		
		if sys.platform == 'linux2' or (sys.platform.find('freebsd') != -1 and self.linuxProcFsLocation != False):
			self.checksLogger.debug('getMemoryUsage: %s' % ('linux2' if sys.platform == 'linux2' else 'freebsd (meminfo)'))
			
			try:
				self.checksLogger.debug('getMemoryUsage: attempting open')
				
				if sys.platform == 'linux2':
					meminfoProc = open('/proc/meminfo', 'r')
				else:
					meminfoProc = open(self.linuxProcFsLocation + '/meminfo', 'r')
				
				lines = meminfoProc.readlines()
				
			except IOError, e:
				self.checksLogger.error('getMemoryUsage: exception = ' + str(e))
				return False
				
			self.checksLogger.debug('getMemoryUsage: Popen success, parsing')
			
			meminfoProc.close()
			
			self.checksLogger.debug('getMemoryUsage: open success, parsing')
			
			regexp = re.compile(r'([0-9]+)') # We run this several times so one-time compile now
			
			meminfo = {}
			
			self.checksLogger.debug('getMemoryUsage: parsing, looping')
			
			# Loop through and extract the numerical values
			for line in lines:
				values = line.split(':')
				
				try:
					# Picks out the key (values[0]) and makes a list with the value as the meminfo value (values[1])
					# We are only interested in the KB data so regexp that out
					match = re.search(regexp, values[1])
	
					if match != None:
						meminfo[str(values[0])] = match.group(0)
					
				except IndexError:
					break
					
			self.checksLogger.debug('getMemoryUsage: parsing, looped')
			
			memData = {}
			
			# Phys
			try:
				self.checksLogger.debug('getMemoryUsage: formatting (phys)')
				
				physTotal = int(meminfo['MemTotal'])
				physFree = int(meminfo['MemFree'])
				physUsed = physTotal - physFree
				
				# Convert to MB
				memData['physFree'] = physFree / 1024
				memData['physUsed'] = physUsed / 1024
				memData['cached'] = int(meminfo['Cached']) / 1024
								
			# Stops the agent crashing if one of the meminfo elements isn't set
			except IndexError:
				self.checksLogger.debug('getMemoryUsage: formatting (phys) IndexError - Cached, MemTotal or MemFree not present')
				
			except KeyError:
				self.checksLogger.debug('getMemoryUsage: formatting (phys) KeyError - Cached, MemTotal or MemFree not present')
			
			self.checksLogger.debug('getMemoryUsage: formatted (phys)')
			
			# Swap
			try:
				self.checksLogger.debug('getMemoryUsage: formatting (swap)')
				
				swapTotal = int(meminfo['SwapTotal'])
				swapFree = int(meminfo['SwapFree'])
				swapUsed = swapTotal - swapFree
				
				# Convert to MB
				memData['swapFree'] = swapFree / 1024
				memData['swapUsed'] = swapUsed / 1024
								
			# Stops the agent crashing if one of the meminfo elements isn't set
			except IndexError:
				self.checksLogger.debug('getMemoryUsage: formatting (swap) IndexErro) - SwapTotal or SwapFree not present')
				
			except KeyError:
				self.checksLogger.debug('getMemoryUsage: formatting (swap) KeyError - SwapTotal or SwapFree not present')
			
			self.checksLogger.debug('getMemoryUsage: formatted (swap), completed, returning')
			
			return memData	
			
		elif sys.platform.find('freebsd') != -1 and self.linuxProcFsLocation == False:
			self.checksLogger.debug('getMemoryUsage: freebsd (native)')
			
			try:
				self.checksLogger.debug('getMemoryUsage: attempting Popen (sysctl)')				
				physTotal = subprocess.Popen(['sysctl', '-n', 'hw.physmem'], stdout = subprocess.PIPE, close_fds = True).communicate()[0]
				
				self.checksLogger.debug('getMemoryUsage: attempting Popen (vmstat)')				
				vmstat = subprocess.Popen(['vmstat', '-H'], stdout = subprocess.PIPE, close_fds = True).communicate()[0]
				
				self.checksLogger.debug('getMemoryUsage: attempting Popen (swapinfo)')
				swapinfo = subprocess.Popen(['swapinfo', '-k'], stdout = subprocess.PIPE, close_fds = True).communicate()[0]

			except Exception, e:
				import traceback
				self.checksLogger.error('getMemoryUsage: exception = ' + traceback.format_exc())
				
				return False	
				
			self.checksLogger.debug('getMemoryUsage: Popen success, parsing')

			# First we parse the information about the real memory
			lines = vmstat.split('\n')
			physParts = re.findall(r'([0-9]\d+)', lines[2])
	
			physTotal = int(physTotal.strip()) / 1024 # physFree is returned in B, but we need KB so we convert it
			physFree = int(physParts[1])
			physUsed = int(physTotal - physFree)
	
			self.checksLogger.debug('getMemoryUsage: parsed vmstat')
	
			# And then swap
			lines = swapinfo.split('\n')
			swapParts = re.findall(r'(\d+)', lines[1])
			
			# Convert evrything to MB
			physUsed = int(physUsed) / 1024
			physFree = int(physFree) / 1024
			swapUsed = int(swapParts[3]) / 1024
			swapFree = int(swapParts[4]) / 1024
	
			self.checksLogger.debug('getMemoryUsage: parsed swapinfo, completed, returning')
	
			return {'physUsed' : physUsed, 'physFree' : physFree, 'swapUsed' : swapUsed, 'swapFree' : swapFree, 'cached' : 'NULL'}
			
		elif sys.platform == 'darwin':
			self.checksLogger.debug('getMemoryUsage: darwin')
			
			try:
				self.checksLogger.debug('getMemoryUsage: attempting Popen (top)')				
				top = subprocess.Popen(['top', '-l 1'], stdout=subprocess.PIPE, close_fds=True).communicate()[0]
				
				self.checksLogger.debug('getMemoryUsage: attempting Popen (sysctl)')
				sysctl = subprocess.Popen(['sysctl', 'vm.swapusage'], stdout=subprocess.PIPE, close_fds=True).communicate()[0]
				
			except Exception, e:
				import traceback
				self.checksLogger.error('getMemoryUsage: exception = ' + traceback.format_exc())
				return False
			
			self.checksLogger.debug('getMemoryUsage: Popen success, parsing')
			
			# Deal with top			
			lines = top.split('\n')
			physParts = re.findall(r'([0-9]\d+)', lines[5])
			
			self.checksLogger.debug('getMemoryUsage: parsed top')
			
			# Deal with sysctl
			swapParts = re.findall(r'([0-9]+\.\d+)', sysctl)
			
			self.checksLogger.debug('getMemoryUsage: parsed sysctl, completed, returning')
			
			return {'physUsed' : physParts[3], 'physFree' : physParts[4], 'swapUsed' : swapParts[1], 'swapFree' : swapParts[2], 'cached' : 'NULL'}	
			
		else:
			return False
	
	def getMySQLStatus(self):
		self.checksLogger.debug('getMySQLStatus: start')
		
		if 'MySQLServer' in self.agentConfig and 'MySQLUser' in self.agentConfig and 'MySQLPass' in self.agentConfig and self.agentConfig['MySQLServer'] != '' and self.agentConfig['MySQLUser'] != '' and self.agentConfig['MySQLPass'] != '':
		
			self.checksLogger.debug('getMySQLStatus: config')
			
			# Try import MySQLdb - http://sourceforge.net/projects/mysql-python/files/
			try:
				import MySQLdb
			
			except ImportError, e:
				self.checksLogger.debug('getMySQLStatus: unable to import MySQLdb')
				return False
				
			# Connect
			try:
				db = MySQLdb.connect(self.agentConfig['MySQLServer'], self.agentConfig['MySQLUser'], self.agentConfig['MySQLPass'])
				
			except MySQLdb.OperationalError, message:
				
				self.checksLogger.debug('getMySQLStatus: MySQL connection error: ' + str(message))
				return False
			
			self.checksLogger.debug('getMySQLStatus: connected')
			
			self.checksLogger.debug('getMySQLStatus: getting Connections')
			
			# Connections
			try:
				cursor = db.cursor()
				cursor.execute('SHOW GLOBAL STATUS LIKE "Connections"')
				result = cursor.fetchone()
				
			except MySQLdb.OperationalError, message:
			
				self.checksLogger.debug('getMySQLStatus: MySQL query error when getting Connections: ' + str(message))
		
			if self.mysqlConnectionsStore == None:
				
				self.checksLogger.debug('getMySQLStatus: mysqlConnectionsStore unset storing for first time')
				
				self.mysqlConnectionsStore = result[1]
				
				connections = 0
				
			else:
		
				self.checksLogger.debug('getMySQLStatus: mysqlConnectionsStore set so calculating')
				self.checksLogger.debug('getMySQLStatus: self.mysqlConnectionsStore = ' + str(self.mysqlConnectionsStore))
				self.checksLogger.debug('getMySQLStatus: result = ' + str(result[1]))
				
				connections = float(float(result[1]) - float(self.mysqlConnectionsStore)) / 60
				
				self.mysqlConnectionsStore = result[1]
				
			self.checksLogger.debug('getMySQLStatus: connections = ' + str(connections))
			
			self.checksLogger.debug('getMySQLStatus: getting Connections - done')
			
			self.checksLogger.debug('getMySQLStatus: getting Created_tmp_disk_tables')
				
			# Created_tmp_disk_tables
			try:
				cursor = db.cursor()
				cursor.execute('SHOW GLOBAL STATUS LIKE "Created_tmp_disk_tables"')
				result = cursor.fetchone()
				
			except MySQLdb.OperationalError, message:
			
				self.checksLogger.debug('getMySQLStatus: MySQL query error when getting Created_tmp_disk_tables: ' + str(message))
		
			if self.mysqlCreatedTmpDiskTablesStore == None:
				
				self.checksLogger.debug('getMySQLStatus: mysqlCreatedTmpDiskTablesStore unset so storing for first time')
				
				self.mysqlCreatedTmpDiskTablesStore = result[1]
				
				createdTmpDiskTables = 0
				
			else:
		
				self.checksLogger.debug('getMySQLStatus: mysqlCreatedTmpDiskTablesStore set so calculating')
				self.checksLogger.debug('getMySQLStatus: self.mysqlCreatedTmpDiskTablesStore = ' + str(self.mysqlCreatedTmpDiskTablesStore))
				self.checksLogger.debug('getMySQLStatus: result = ' + str(result[1]))
				
				createdTmpDiskTables = float(float(result[1]) - float(self.mysqlCreatedTmpDiskTablesStore)) / 60
				
				self.mysqlCreatedTmpDiskTablesStore = result[1]
				
			self.checksLogger.debug('getMySQLStatus: createdTmpDiskTables = ' + str(createdTmpDiskTables))
			
			self.checksLogger.debug('getMySQLStatus: getting Created_tmp_disk_tables - done')
			
			self.checksLogger.debug('getMySQLStatus: getting Max_used_connections')
				
			# Max_used_connections
			try:
				cursor = db.cursor()
				cursor.execute('SHOW GLOBAL STATUS LIKE "Max_used_connections"')
				result = cursor.fetchone()
				
			except MySQLdb.OperationalError, message:
			
				self.checksLogger.debug('getMySQLStatus: MySQL query error when getting Max_used_connections: ' + str(message))
				
			maxUsedConnections = result[1]
			
			self.checksLogger.debug('getMySQLStatus: maxUsedConnections = ' + str(createdTmpDiskTables))
			
			self.checksLogger.debug('getMySQLStatus: getting Max_used_connections - done')
			
			self.checksLogger.debug('getMySQLStatus: getting Open_files')
			
			# Open_files
			try:
				cursor = db.cursor()
				cursor.execute('SHOW GLOBAL STATUS LIKE "Open_files"')
				result = cursor.fetchone()
				
			except MySQLdb.OperationalError, message:
			
				self.checksLogger.debug('getMySQLStatus: MySQL query error when getting Open_files: ' + str(message))
				
			openFiles = result[1]
			
			self.checksLogger.debug('getMySQLStatus: openFiles = ' + str(openFiles))
			
			self.checksLogger.debug('getMySQLStatus: getting Open_files - done')
			
			self.checksLogger.debug('getMySQLStatus: getting Slow_queries')
			
			# Slow_queries
			try:
				cursor = db.cursor()
				cursor.execute('SHOW GLOBAL STATUS LIKE "Slow_queries"')
				result = cursor.fetchone()
				
			except MySQLdb.OperationalError, message:
			
				self.checksLogger.debug('getMySQLStatus: MySQL query error when getting Slow_queries: ' + str(message))
		
			if self.mysqlSlowQueriesStore == None:
				
				self.checksLogger.debug('getMySQLStatus: mysqlSlowQueriesStore unset so storing for first time')
				
				self.mysqlSlowQueriesStore = result[1]
				
				slowQueries = 0
				
			else:
		
				self.checksLogger.debug('getMySQLStatus: mysqlSlowQueriesStore set so calculating')
				self.checksLogger.debug('getMySQLStatus: self.mysqlSlowQueriesStore = ' + str(self.mysqlSlowQueriesStore))
				self.checksLogger.debug('getMySQLStatus: result = ' + str(result[1]))
				
				slowQueries = float(float(result[1]) - float(self.mysqlSlowQueriesStore)) / 60
				
				self.mysqlSlowQueriesStore = result[1]
				
			self.checksLogger.debug('getMySQLStatus: slowQueries = ' + str(slowQueries))
			
			self.checksLogger.debug('getMySQLStatus: getting Slow_queries - done')
			
			self.checksLogger.debug('getMySQLStatus: getting Table_locks_waited')
				
			# Table_locks_waited
			try:
				cursor = db.cursor()
				cursor.execute('SHOW GLOBAL STATUS LIKE "Table_locks_waited"')
				result = cursor.fetchone()
				
			except MySQLdb.OperationalError, message:
			
				self.checksLogger.debug('getMySQLStatus: MySQL query error when getting Table_locks_waited: ' + str(message))
		
			if self.mysqlTableLocksWaited == None:
				
				self.checksLogger.debug('getMySQLStatus: mysqlTableLocksWaited unset so storing for first time')
				
				self.mysqlTableLocksWaited = result[1]
				
				tableLocksWaited = 0
				
			else:
		
				self.checksLogger.debug('getMySQLStatus: mysqlTableLocksWaited set so calculating')
				self.checksLogger.debug('getMySQLStatus: self.mysqlTableLocksWaited = ' + str(self.mysqlTableLocksWaited))
				self.checksLogger.debug('getMySQLStatus: result = ' + str(result[1]))
				
				tableLocksWaited = float(float(result[1]) - float(self.mysqlTableLocksWaited)) / 60
				
				self.mysqlTableLocksWaited = result[1]
				
			self.checksLogger.debug('getMySQLStatus: tableLocksWaited = ' + str(tableLocksWaited))
			
			self.checksLogger.debug('getMySQLStatus: getting Table_locks_waited - done')
			
			self.checksLogger.debug('getMySQLStatus: getting Threads_connected')
				
			# Threads_connected
			try:
				cursor = db.cursor()
				cursor.execute('SHOW GLOBAL STATUS LIKE "Threads_connected"')
				result = cursor.fetchone()
				
			except MySQLdb.OperationalError, message:
			
				self.checksLogger.debug('getMySQLStatus: MySQL query error when getting Threads_connected: ' + str(message))
				
			threadsConnected = result[1]
			
			self.checksLogger.debug('getMySQLStatus: threadsConnected = ' + str(threadsConnected))
			
			self.checksLogger.debug('getMySQLStatus: getting Threads_connected - done')
			
			self.checksLogger.debug('getMySQLStatus: getting Seconds_Behind_Master')
			
			# Seconds_Behind_Master
			try:
				cursor = db.cursor()
				cursor.execute('SHOW SLAVE STATUS')
				result = cursor.fetchone()
				
			except MySQLdb.OperationalError, message:
			
				self.checksLogger.debug('getMySQLStatus: MySQL query error when getting SHOW SLAVE STATUS: ' + str(message))
			
			if result != None:
				try:
					secondsBehindMaster = result[28]
				
					self.checksLogger.debug('getMySQLStatus: secondsBehindMaster = ' + str(secondsBehindMaster))
					
				except IndexError, e:					
					secondsBehindMaster = None
					
					self.checksLogger.debug('getMySQLStatus: secondsBehindMaster empty')
			
			else:
				secondsBehindMaster = None
				
				self.checksLogger.debug('getMySQLStatus: secondsBehindMaster empty')
			
			self.checksLogger.debug('getMySQLStatus: getting Seconds_Behind_Master - done')
			
			return {'connections' : connections, 'createdTmpDiskTables' : createdTmpDiskTables, 'maxUsedConnections' : maxUsedConnections, 'openFiles' : openFiles, 'slowQueries' : slowQueries, 'tableLocksWaited' : tableLocksWaited, 'threadsConnected' : threadsConnected, 'secondsBehindMaster' : secondsBehindMaster}

		else:			
			
			self.checksLogger.debug('getMySQLStatus: config not set')
			return False
		
	
	def getNginxStatus(self):
		self.checksLogger.debug('getNginxStatus: start')
		
		if 'nginxStatusUrl' in self.agentConfig and self.agentConfig['nginxStatusUrl'] != 'http://www.example.com/nginx_status':	# Don't do it if the status URL hasn't been provided
			self.checksLogger.debug('getNginxStatus: config set')
			
			try: 
				self.checksLogger.debug('getNginxStatus: attempting urlopen')
				
				request = urllib2.urlopen(self.agentConfig['nginxStatusUrl'])
				response = request.read()
				
			except urllib2.HTTPError, e:
				self.checksLogger.error('Unable to get Nginx status - HTTPError = ' + str(e))
				return False
				
			except urllib2.URLError, e:
				self.checksLogger.error('Unable to get Nginx status - URLError = ' + str(e))
				return False
				
			except httplib.HTTPException, e:
				self.checksLogger.error('Unable to get Nginx status - HTTPException = ' + str(e))
				return False
				
			except Exception, e:
				import traceback
				self.checksLogger.error('Unable to get Nginx status - Exception = ' + traceback.format_exc())
				return False
				
			self.checksLogger.debug('getNginxStatus: urlopen success, start parsing')
			
			# Thanks to http://hostingfu.com/files/nginx/nginxstats.py for this code
			
			self.checksLogger.debug('getNginxStatus: parsing connections')
			
			# Connections
			parsed = re.search(r'Active connections:\s+(\d+)', response)
			connections = int(parsed.group(1))
			
			self.checksLogger.debug('getNginxStatus: parsed connections')
			self.checksLogger.debug('getNginxStatus: parsing reqs')
			
			# Requests per second
			parsed = re.search(r'\s*(\d+)\s+(\d+)\s+(\d+)', response)
			requests = int(parsed.group(3))
			
			self.checksLogger.debug('getNginxStatus: parsed reqs')
			
			if self.nginxRequestsStore == None:
				
				self.checksLogger.debug('getNginxStatus: no reqs so storing for first time')
				
				self.nginxRequestsStore = requests
				
				requestsPerSecond = 0
				
			else:
				
				self.checksLogger.debug('getNginxStatus: reqs stored so calculating')
				self.checksLogger.debug('getNginxStatus: self.nginxRequestsStore = ' + str(self.nginxRequestsStore))
				self.checksLogger.debug('getNginxStatus: requests = ' + str(requests))
				
				requestsPerSecond = float(requests - self.nginxRequestsStore) / 60
				
				self.checksLogger.debug('getNginxStatus: requestsPerSecond = ' + str(requestsPerSecond))
				
				self.nginxRequestsStore = requests
			
			if connections != None and requestsPerSecond != None:
			
				self.checksLogger.debug('getNginxStatus: returning with data')
				
				return {'connections' : connections, 'reqPerSec' : requestsPerSecond}
			
			else:
			
				self.checksLogger.debug('getNginxStatus: returning without data')
				
				return False
			
		else:
			self.checksLogger.debug('getNginxStatus: config not set')
			
			return False
			
	def getNetworkTraffic(self):
		self.checksLogger.debug('getNetworkTraffic: start')

		if sys.platform == 'linux2':
			self.checksLogger.debug('getNetworkTraffic: linux2')
			
			try:
				self.checksLogger.debug('getNetworkTraffic: attempting open')
				
				proc = open('/proc/net/dev', 'r')
				lines = proc.readlines()
				
			except IOError, e:
				self.checksLogger.error('getNetworkTraffic: exception = ' + str(e))
				return False
			
			proc.close()
			
			self.checksLogger.debug('getNetworkTraffic: open success, parsing')
			
			columnLine = lines[1]
			_, receiveCols , transmitCols = columnLine.split('|')
			receiveCols = map(lambda a:'recv_' + a, receiveCols.split())
			transmitCols = map(lambda a:'trans_' + a, transmitCols.split())
			
			cols = receiveCols + transmitCols
			
			self.checksLogger.debug('getNetworkTraffic: parsing, looping')
			
			faces = {}
			for line in lines[2:]:
				if line.find(':') < 0: continue
				face, data = line.split(':')
				faceData = dict(zip(cols, data.split()))
				faces[face] = faceData
			
			self.checksLogger.debug('getNetworkTraffic: parsed, looping')
			
			interfaces = {}
			
			# Now loop through each interface
			for face in faces:
				key = face.strip()
				
				# We need to work out the traffic since the last check so first time we store the current value
				# then the next time we can calculate the difference
				if key in self.networkTrafficStore:
					interfaces[key] = {}
					interfaces[key]['recv_bytes'] = long(faces[face]['recv_bytes']) - long(self.networkTrafficStore[key]['recv_bytes'])
					interfaces[key]['trans_bytes'] = long(faces[face]['trans_bytes']) - long(self.networkTrafficStore[key]['trans_bytes'])
					
					interfaces[key]['recv_bytes'] = str(interfaces[key]['recv_bytes'])
					interfaces[key]['trans_bytes'] = str(interfaces[key]['trans_bytes'])
					
					# And update the stored value to subtract next time round
					self.networkTrafficStore[key]['recv_bytes'] = faces[face]['recv_bytes']
					self.networkTrafficStore[key]['trans_bytes'] = faces[face]['trans_bytes']
					
				else:
					self.networkTrafficStore[key] = {}
					self.networkTrafficStore[key]['recv_bytes'] = faces[face]['recv_bytes']
					self.networkTrafficStore[key]['trans_bytes'] = faces[face]['trans_bytes']
		
			self.checksLogger.debug('getNetworkTraffic: completed, returning')
					
			return interfaces
	
		elif sys.platform.find('freebsd') != -1:
			self.checksLogger.debug('getNetworkTraffic: freebsd')
			
			try:
				self.checksLogger.debug('getNetworkTraffic: attempting Popen (netstat)')
				netstat = subprocess.Popen(['netstat', '-nbid', ' grep Link'], stdout = subprocess.PIPE, close_fds = True)
				
				self.checksLogger.debug('getNetworkTraffic: attempting Popen (grep)')
				grep = subprocess.Popen(['grep', 'Link'], stdin = netstat.stdout, stdout = subprocess.PIPE, close_fds = True).communicate()[0]
				
			except Exception, e:
				import traceback
				self.checksLogger.error('getNetworkTraffic: exception = ' + traceback.format_exc())
				
				return False
			
			self.checksLogger.debug('getNetworkTraffic: open success, parsing')
			
			lines = grep.split('\n')
			
			# Loop over available data for each inteface
			faces = {}
			for line in lines:
				line = re.split(r'\s+', line)
				length = len(line)
				
				if length == 13:
					faceData = {'recv_bytes': line[6], 'trans_bytes': line[9], 'drops': line[10], 'errors': long(line[5]) + long(line[8])}
				elif length == 12:
					faceData = {'recv_bytes': line[5], 'trans_bytes': line[8], 'drops': line[9], 'errors': long(line[4]) + long(line[7])}
				else:
					# Malformed or not enough data for this interface, so we skip it
					continue
				
				face = line[0]
				faces[face] = faceData
				
			self.checksLogger.debug('getNetworkTraffic: parsed, looping')
				
			interfaces = {}
			
			# Now loop through each interface
			for face in faces:
				key = face.strip()
				
				# We need to work out the traffic since the last check so first time we store the current value
				# then the next time we can calculate the difference
				if key in self.networkTrafficStore:
					interfaces[key] = {}
					interfaces[key]['recv_bytes'] = long(faces[face]['recv_bytes']) - long(self.networkTrafficStore[key]['recv_bytes'])
					interfaces[key]['trans_bytes'] = long(faces[face]['trans_bytes']) - long(self.networkTrafficStore[key]['trans_bytes'])
					
					interfaces[key]['recv_bytes'] = str(interfaces[key]['recv_bytes'])
					interfaces[key]['trans_bytes'] = str(interfaces[key]['trans_bytes'])
					
					# And update the stored value to subtract next time round
					self.networkTrafficStore[key]['recv_bytes'] = faces[face]['recv_bytes']
					self.networkTrafficStore[key]['trans_bytes'] = faces[face]['trans_bytes']
					
				else:
					self.networkTrafficStore[key] = {}
					self.networkTrafficStore[key]['recv_bytes'] = faces[face]['recv_bytes']
					self.networkTrafficStore[key]['trans_bytes'] = faces[face]['trans_bytes']
		
			self.checksLogger.debug('getNetworkTraffic: completed, returning')
	
			return interfaces

		else:		
			self.checksLogger.debug('getNetworkTraffic: other platform, returning')
		
			return False
		
	def getNetworkLatency(self):
		self.checksLogger.debug('getNetworkLatency: start')
		
		if len(self.agentConfig['latencyIpAddresses']) > 0:	# Only start checks if any IPv4 / IPv6 address has been set in the config file
			self.checksLogger.debug('getNetworkLatency: config set')
			
			if sys.platform == 'linux2':
				self.checksLogger.debug('getNetworkLatency: linux2')
				
				packetStatsRe = re.compile(r'(\d+) packets transmitted, (\d+) received, (.*?)% packet loss, time (\d+)ms')
				responseStatsRe = re.compile(r'rtt min/avg/max/mdev = (\d+\.\d+)/(\d+\.\d+)/(\d+\.\d+)/(\d+\.\d+) ms')
				
			elif sys.platform.find('freebsd') != -1:
				self.checksLogger.debug('getNetworkLatency: freebsd')
				
				packetStatsRe = re.compile(r'(\d+) packets transmitted, (\d+) packets received, (.*?)% packet loss')
				responseStatsRe = re.compile(r'round-trip min/avg/max/(stddev|std-dev) = (\d+\.\d+)/(\d+\.\d+)/(\d+\.\d+)/(\d+\.\d+) ms')
				
			else:		
				self.checksLogger.debug('getNetworkLatency: other platform, returning')
			
				return False
		
			# Compile frequently used regular expressions
			ipv4Re = re.compile(r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$')
			ipv6Re = re.compile(r'^(?:[A-F0-9]{1,4}:){7}[A-F0-9]{1,4}$', re.IGNORECASE)
			
			try:
				response = {}
				for entry in self.agentConfig['latencyIpAddresses']:
					address = entry[1]

					if re.match(ipv4Re, address) != None:
						command = 'ping'
					elif re.match(ipv6Re, address) != None:
						command = 'ping6'
					else:
						# Not a valid IPv4 or IPv6 address, skip this iteration
						continue

					response[address] = subprocess.Popen([command, '-c', '5', address], stdout = subprocess.PIPE, close_fds = True).communicate()[0]
			
			except Exception, e:
				import traceback
				self.checksLogger.error('getNetworkLatency: exception = ' + traceback.format_exc())
				
				return False
			
		else:
			self.checksLogger.debug('getNetworkLatency: config not set')
			
			return False
		
		self.checksLogger.debug('getNetworkLatency: Popen success, parsing')

		latencyStatus = {}
		
		# Loop over responses and parse the data
		for address in response:
			packetStats = re.search(packetStatsRe, response[address])
			responseStats = re.search(responseStatsRe, response[address])
			
			# Valid response
			if packetStats != None:
				latencyStatus[address] = {}
				latencyStatus[address]['trans_packets'] = packetStats.group(1)
				latencyStatus[address]['recv_packets'] = packetStats.group(2)
				latencyStatus[address]['packet_loss'] = packetStats.group(3)
				
				if responseStats != None:
					latencyStatus[address]['response_min'] = responseStats.group(2)
					latencyStatus[address]['response_max'] = responseStats.group(4)
					latencyStatus[address]['response_avg'] = responseStats.group(3)
				else:
					latencyStatus[address]['response_min'] = ''
					latencyStatus[address]['response_max'] = ''
					latencyStatus[address]['response_avg'] = ''
					
		self.checksLogger.debug('getNetworkLatency: completed, returning')
				
		return latencyStatus
		
	def getProcesses(self):
		self.checksLogger.debug('getProcesses: start')
		
		# Memory logging (case 27152)
		if self.agentConfig['debugMode'] and sys.platform == 'linux2':
			mem = subprocess.Popen(['free', '-m'], stdout=subprocess.PIPE, close_fds=True).communicate()[0]
			self.checksLogger.debug('getProcesses: memory before Popen - ' + str(mem))
		
		# Get output from ps
		try:
			self.checksLogger.debug('getProcesses: attempting Popen')
			
			ps = subprocess.Popen(['ps', 'aux'], stdout=subprocess.PIPE, close_fds=True).communicate()[0]
			
		except Exception, e:
			import traceback
			self.checksLogger.error('getProcesses: exception = ' + traceback.format_exc())
			return False
		
		self.checksLogger.debug('getProcesses: Popen success, parsing')
		
		# Memory logging (case 27152)
		if self.agentConfig['debugMode'] and sys.platform == 'linux2':
			mem = subprocess.Popen(['free', '-m'], stdout=subprocess.PIPE, close_fds=True).communicate()[0]
			self.checksLogger.debug('getProcesses: memory after Popen - ' + str(mem))
		
		# Split out each process
		processLines = ps.split('\n')
		
		del processLines[0] # Removes the headers
		processLines.pop() # Removes a trailing empty line
		
		processes = []
		
		self.checksLogger.debug('getProcesses: Popen success, parsing, looping')
		
		for line in processLines:
			line = line.split(None, 10)
			processes.append(line)
		
		self.checksLogger.debug('getProcesses: completed, returning')
			
		return processes
		
	def doPostBack(self, postBackData):
		self.checksLogger.debug('doPostBack: start')	
		
		try: 
			self.checksLogger.debug('doPostBack: attempting postback: ' + self.agentConfig['sdUrl'])
			
			# Build the request handler
			request = urllib2.Request(self.agentConfig['sdUrl'] + '/postback/', postBackData, { 'User-Agent' : 'Server Density Agent' })
			
			# Do the request, log any errors
			response = urllib2.urlopen(request)
			
			if self.agentConfig['debugMode']:
				print response.read()
				
		except urllib2.HTTPError, e:
			self.checksLogger.error('doPostBack: HTTPError = ' + str(e))
			return False
			
		except urllib2.URLError, e:
			self.checksLogger.error('doPostBack: URLError = ' + str(e))
			return False
			
		except httplib.HTTPException, e: # Added for case #26701
			self.checksLogger.error('doPostBack: HTTPException')
			return False
				
		except Exception, e:
			import traceback
			self.checksLogger.error('doPostBack: Exception = ' + traceback.format_exc())
			return False
			
		self.checksLogger.debug('doPostBack: completed')
	
	def doChecks(self, sc, firstRun, systemStats=False):
		self.checksLogger = logging.getLogger('checks')
		self.linuxProcFsLocation = self.getMountedLinuxProcFsLocation()
		
		self.checksLogger.debug('doChecks: start')
				
		# Do the checks
		apacheStatus = self.getApacheStatus()
		diskUsage = self.getDiskUsage()
		loadAvrgs = self.getLoadAvrgs()
		memory = self.getMemoryUsage()
		mysqlStatus = self.getMySQLStatus()
		networkTraffic = self.getNetworkTraffic()
		nginxStatus = self.getNginxStatus()
		processes = self.getProcesses()
		latencyStatus = self.getNetworkLatency()	
		
		self.checksLogger.debug('doChecks: checks success, build payload')
		self.checksLogger.debug({'agentKey' : self.agentConfig['agentKey'], 'agentVersion' : self.agentConfig['version'], 'diskUsage' : diskUsage, 'loadAvrg' : loadAvrgs['1'], 'memPhysUsed' : memory['physUsed'], 'memPhysFree' : memory['physFree'], 'memSwapUsed' : memory['swapUsed'], 'memSwapFree' : memory['swapFree'], 'memCached' : memory['cached'], 'networkTraffic' : networkTraffic, 'processes' : processes})
		
		checksData = {'agentKey' : self.agentConfig['agentKey'], 'agentVersion' : self.agentConfig['version'], 'diskUsage' : diskUsage, 'loadAvrg' : loadAvrgs['1'], 'memPhysUsed' : memory['physUsed'], 'memPhysFree' : memory['physFree'], 'memSwapUsed' : memory['swapUsed'], 'memSwapFree' : memory['swapFree'], 'memCached' : memory['cached'], 'networkTraffic' : networkTraffic, 'processes' : processes}
		
		self.checksLogger.debug('doChecks: payload built, build optional payloads')
		
		# Apache Status
		if apacheStatus != False:			
			checksData['apacheReqPerSec'] = apacheStatus['reqPerSec']
			checksData['apacheBusyWorkers'] = apacheStatus['busyWorkers']
			checksData['apacheIdleWorkers'] = apacheStatus['idleWorkers']
			
			self.checksLogger.debug('doChecks: built optional payload apacheStatus')
		
		# MySQL Status
		if mysqlStatus != False:
			
			checksData['mysqlConnections'] = mysqlStatus['connections']
			checksData['mysqlCreatedTmpDiskTables'] = mysqlStatus['createdTmpDiskTables']
			checksData['mysqlMaxUsedConnections'] = mysqlStatus['maxUsedConnections']
			checksData['mysqlOpenFiles'] = mysqlStatus['openFiles']
			checksData['mysqlSlowQueries'] = mysqlStatus['slowQueries']
			checksData['mysqlTableLocksWaited'] = mysqlStatus['tableLocksWaited']
			checksData['mysqlThreadsConnected'] = mysqlStatus['threadsConnected']
			
			if mysqlStatus['secondsBehindMaster'] != None:
				checksData['mysqlSecondsBehindMaster'] = mysqlStatus['secondsBehindMaster']
		
		# Nginx Status
		if nginxStatus != False:
			checksData['nginxConnections'] = nginxStatus['connections']
			checksData['nginxReqPerSec'] = nginxStatus['reqPerSec']
			
		# Include system stats on first postback
		if firstRun == True:
			checksData['systemStats'] = systemStats
			self.checksLogger.debug('doChecks: built optional payload systemStats')
		
		self.checksLogger.debug('doChecks: payloads built, convert to json')
		
		# Post back the data
		if int(pythonVersion[1]) >= 6:
			self.checksLogger.debug('doChecks: json convert')
			
			payload = json.dumps(checksData)
		
		else:
			self.checksLogger.debug('doChecks: minjson convert')
			
			payload = minjson.write(checksData)
			
		self.checksLogger.debug('doChecks: json converted, hash')
		
		payloadHash = md5.new(payload).hexdigest()
		postBackData = urllib.urlencode({'payload' : payload, 'hash' : payloadHash})

		self.checksLogger.debug('doChecks: hashed, doPostBack')

		self.doPostBack(postBackData)
		
		self.checksLogger.debug('doChecks: posted back, reschedule')
		
		sc.enter(self.agentConfig['checkFreq'], 1, self.doChecks, (sc, False))	
		
	def getMountedLinuxProcFsLocation(self):
		self.checksLogger.debug('getLoadAvrgs: attempting to fetch mounted partitions')
		
		# Lets check if the Linux like style procfs is mounted
		mountedPartitions = subprocess.Popen(['mount'], stdout = subprocess.PIPE, close_fds = True).communicate()[0]
		location = re.search(r'linprocfs on (.*?) \(.*?\)', mountedPartitions)
		
		# Linux like procfs file system is not mounted so we return False, else we return mount point location
		if location == None:
			return False

		location = location.group(1)
		return location
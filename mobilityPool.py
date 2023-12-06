#!/usr/bin/env python

'Setting the position of nodes and providing mobility'

import os
import sys
from time import time, sleep
import random
import math
from threading import Thread as thread
import threading
import socket
import logging
from mininet.node import Controller
from mininet.log import setLogLevel, info
from mn_wifi.replaying import ReplayingMobility
from mn_wifi.cli import CLI
from mn_wifi.net import Mininet_wifi
from mn_wifi.link import wmediumd, adhoc
from mn_wifi.wmediumdConnector import interference
from mn_wifi.mobility import ConfigMobLinks
from mn_wifi.node import Station, AP
from mn_wifi.clean import Cleanup as CleanupWifi
from mn_wifi.plot import PlotGraph

ITERATIONS_MOVE = 10

import socket
import random
from time import sleep

logger = logging.getLogger('my_logger')
logger.setLevel(logging.DEBUG)

if os.path.exists('drone.log'):
    os.remove('drone.log')

file_handler = logging.FileHandler('drone.log')
file_handler.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)


formatter = logging.Formatter('%(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(console_handler)


_kill_list = []
_kill_list2 = []
unit = 10.0
_master = None
_poll = None

class Drone:
    def __init__(self, droneId, station):
        self.droneId = droneId
        self.station = station
        self.weights = [0.9, 0.1]
        self.poll_number = 0

    def get_station(self):
        return self.station

    def get_droneId(self):
        return self.droneId
    
    def vote(self):
        self.poll_number += 1
        if (self.droneId in _kill_list2) or (self.droneId in _kill_list and self.poll_number == 1):
            vote = None
        else:
            options = ["ADVANCE", "RETURN"]
            vote = random.choices(options, self.weights, k=1)[0]
        return vote

    def set_weights(self, option):
        self.weights = [1.0, 0.0] if option == "ADVANCE" else [0.0, 1.0]

class MasterDrone(Drone):
    def __init__(self, droneId, station):
        super().__init__(droneId, station)
        self.otherDrones = []
        self.sockets = {}

    def get_all_drones(self):
        return self.otherDrones
    
    def add_drone(self, drone):
        self.otherDrones.append(drone)

    def remove_drone(self, droneId):
      for drone in self.otherDrones:
        if drone.droneId == droneId:    
            self.otherDrones.remove(drone)
            self.sockets[droneId].close()
            del self.sockets[droneId]
        else:
            server_send_message(self.sockets[drone.droneId], f"Drone {droneId} was removed from the group.")

    def verify_every_x_seconds(self, x):
        start_time = time()
        verify_message = "Who's there?"
        while self.station.position != (math.floor((len(self.otherDrones))/3)*unit,math.floor((len(self.otherDrones))%3)*unit,0.0):
            for drone in self.otherDrones:
                s = self.sockets[drone.droneId]
                server_send_message(s, verify_message)
            sleep(x)
        for drone in self.otherDrones:
            s = self.sockets[drone.droneId]
            server_send_message(s, verify_message)

class SlaveDrone(Drone):
    def __init__(self, droneId, station, masterDrone=None):
        super().__init__(droneId, station)
        self.masterDrone = masterDrone
        self.s = None

    def open_socket(self, droneId):
        host = '127.0.0.1'
        port = 12345  # Make sure it's within the > 1024 $$ <65535 range
        self.s = socket.socket()
        self.s.connect((host, port))
        self.send_message(f"Connected drone {droneId} to the server.")

    def send_message(self, message):
        self.s.send(str(message).encode('utf-8'))
        logger.info(f"CLIENT {self.droneId} SENT: {message}")

    def receive_message(self):
        data = self.s.recv(1024).decode('utf-8')
        logger.info(f"CLIENT {self.droneId} RECEIVED: {data}")
        return data

    def socket_loop(self):
        while True:
            try:
                msg = self.receive_message()
                if msg == "VOTE":
                    vote = self.vote()
                    self.send_message(f"VOTE: {vote} Drone: {self.droneId}")
                elif msg == "LEAVE":
                    self.send_message("OK. Leaving.")
                    self.close_socket()
                    break
                elif msg == "Who's there?":
                    self.send_message(f"I'm here. Position: {str(self.station.position)}")
            except:
                pass

    def close_socket(self):
        self.s.close()

class Poll():
    def __init__(self, question):
        self.question = question
        self.votes = {"ADVANCE": 0, "RETURN": 0}
        self.votes_buffer = {}
        
    def cast_vote(self, drone_id, vote):
        vote = vote.strip().upper()
        if vote in self.votes:
            self.votes[vote] += 1
    
    def add_vote_to_buffer(self, drone_id, vote):
        self.votes_buffer[drone_id] = vote.upper()

    def run_poll(self, master):
        drones = master.get_all_drones()
        self.reset_poll()
        for drone in drones:
            self.ask_for_vote(master, drone)
        sleep(1)
        non_response = []
        for drone in drones:
            vote = get_vote(drone, self)
            if vote is None or vote.upper() == "NONE":
                self.ask_for_vote(master, drone)
                non_response.append(drone)
            else:
                self.cast_vote(drone.droneId, vote)
        if len(non_response) > 0:
            sleep(0.5)
        for drone in non_response:
            vote = get_vote(drone, self)
            if vote is None or vote.upper() == "NONE":
                logger.info(f"Drone {drone.get_droneId()} did not respond the poll. Removing from the list.")
                master.remove_drone(drone.get_droneId())
                # close socket with drone
            else:
                self.cast_vote(drone.droneId, vote)
                
        return self.calculate_result()

    def ask_for_vote(self, master, drone):
        server_send_message(master.sockets[drone.droneId], "VOTE")
        
    def calculate_result(self):
        advance_votes = self.votes["ADVANCE"]
        return_votes = self.votes["RETURN"]
        
        logger.info(f"Votes to ADVANCE: {advance_votes}")
        logger.info(f"Votes to RETURN: {return_votes}")
        
        if advance_votes > return_votes:
            logger.info("The majority voted to ADVANCE.")
            return "ADVANCE"
        elif return_votes > advance_votes:
            logger.info("The majority voted to RETURN.")
            return "RETURN"
        else:
            logger.info("The vote is tied. A decision needs to be made. Advancing.")
            return "TIE"
        
    def reset_poll(self):
        self.votes = {"ADVANCE": 0, "RETURN": 0}


def get_vote(drone, poll):
    if drone.droneId in poll.votes_buffer:
        return poll.votes_buffer[drone.droneId]
    else:
        return None

def cont_move(master, station, p1, p2):
    # iterate movement
    p_it = p1
    (x1, y1, z1) = p1
    (x2, y2, z2) = p2

    # global positions
    
    for _ in range(ITERATIONS_MOVE):
        (x, y, z) = p_it
        x += (x2-x1)/(ITERATIONS_MOVE)
        y += (y2-y1)/(ITERATIONS_MOVE)
        z += (z2-z1)/(ITERATIONS_MOVE)
        p_it = (round(x,1), round(y,1), round(z,1))
        station.p.append(p_it)

    # repeat last position
    for _ in range(ITERATIONS_MOVE):
        station.p.append(p2)


def open_server(conn, addr, master, poll):
    while True:
        try:
            msg = server_receive_message(conn)
            if msg[:9] == "Connected":
                droneId = int(msg.split()[2])
                master.sockets[droneId] = conn
                res = "Connection established."
                server_send_message(conn, msg)
            elif msg[:4] == "VOTE":
                droneId = int(msg.split()[3])
                vote = msg.split()[1]
                poll.add_vote_to_buffer(droneId, vote)
                res = "Vote received."
                server_send_message(conn, res)
        except:
            logger.info("Error in the connection. Closing.")
            conn.close()


def start_socket(ip='127.0.0.1', port=12345, master=None, poll=None):
    CleanupWifi.socket_port = port

    if ':' in ip:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM, 0)
    else:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((ip, port))
    s.listen(1)

    while True:
        conn, addr = s.accept()
        try:
            thread(target=open_server, args=(conn, addr, master, poll)).start()
        except:
            info("Thread did not start.\n")

def socketServer(**kwargs):
    thread(target=start_socket, kwargs=kwargs).start()


def server_send_message(conn, message):
    conn.send(str(message).encode('utf-8'))
    logger.info(f"SERVER SENT: {message}")

def server_receive_message(conn):
    msg = conn.recv(1024).decode('utf-8')
    logger.info(f"SERVER RECEIVED: {msg}")
    return msg

def topology():
    global positions
    print("Choose between ADVANCE (1) and RETURN (2). Only digits 1/2 are allowed.")
    try: input_option = int(input())
    except:
        print("You did NOT type a digit, so the operation will be aborted: UAVs will will not goto the mission.")
        return "RETURN"
    
    if input_option != 1 and input_option != 2:
        print("You did NOT type either 1 or 2, so the operation will be aborted: UAVs will go to the mission.")
        return "RETURN"

    print("Which drones won't answer the first poll? Select the IDs in a list with comma format. Numbers should be in (1,8). Ex: 1,2,7")
    try: kill_list = input().split(',')
    except:
        print("You did NOT type a digit, so the operation will be aborted: UAVs will will not goto the mission.")
        return "RETURN"

    print("Which drones won't answer both polls? ")
    try: kill_list2 = input().split(',')
    except:
        print("You did NOT type a digit, so the operation will be aborted: UAVs will will not goto the mission.")
        return "RETURN"

    for i in range(len(kill_list)):
        global _kill_list
        _kill_list.append(int(kill_list[i]))
    
    for i in range(len(kill_list2)):
        global _kill_list2
        _kill_list2.append(int(kill_list2[i]))

    "Create a network."
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    net.socketServer = socketServer
    net.start_socket = start_socket

    info("* Creating nodes\n")
    
    # Creating Base and Positions
    ap2 = net.addAccessPoint('P1', ssid='new-ssid', mode='g', channel='2', position='0,0,0')
    ap3 = net.addAccessPoint('P2', ssid='new-ssid', mode='g', channel='2', position='100,100,0')
    ap4 = net.addAccessPoint('P3', ssid='new-ssid', mode='g', channel='2', position='150,150,0')

    # Creating Master Drone
    ap1 = net.addAccessPoint('ap1', ssid='new-ssid', mode='g', channel='1', position='10,10,0')
    sta = net.addStation("S0", mac="00:00:00:00:00:01", ip="10.0.0.1/8", speed=1)
    h1 = net.addHost('h1', ip='10.0.0.3/8')

    # Setting initial parameters for the socket communication
    master = MasterDrone(0, sta)
    _master = master

    # Creating Slave Drones
    for n in range(8):
        sta = net.addStation("S{}".format(n+1), mac="00:00:00:00:00:0{}".format(n+2), ip="10.0.0.{}/8".format(n+2), speed=1)
        drone = SlaveDrone(droneId=(n+1), station=sta, masterDrone=master)
        master.add_drone(drone)
        drone.set_weights("ADVANCE" if input_option == 1 else "RETURN")

    c1 = net.addController('C', controller=Controller)

    info("* Configuring Propagation Model\n")
    net.setPropagationModel(model="logDistance", exp=5)

    info("* Configuring nodes\n")
    net.configureNodes()

    net.addLink(sta, h1)

    # info("* Creating links\n")
    # net.addLink(sta3, cls=adhoc, intf='sta3-wlan0', ssid='adhocNet')
    # net.addLink(sta4, cls=adhoc, intf='sta4-wlan0', ssid='adhocNet')

    net.isReplaying = True

    p1 = (0.0,0.0,0.0)
    p2 = (100.0,100.0,0.0)
    p3 = (150.0,150.0,0.0)

    p1_drone = []
    p2_drone = []
    p3_drone = []

    global unit
    # Slave drones positions
    for it in range(len(master.get_all_drones())+1):
        # set shifts
        dx = math.floor((it)/3)*unit
        dy = math.floor((it)%3)*unit
        # set positions
        (x, y, z) = p1
        p1_drone.append((x+dx, y+dy, z))
        (x, y, z) = p2
        p2_drone.append((x+dx, y+dy, z))
        (x, y, z) = p3
        p3_drone.append((x+dx, y+dy, z))

    # mission begins at P1

    for index in range(len(master.get_all_drones())+1):
        if(index == len(master.get_all_drones())):
            sta = master.get_station()
        else:
            sta = master.get_all_drones()[index].get_station()
        sta.position = p1_drone[index]
        sta.p = [p1_drone[index]]
        # repeat position P1
        for _ in range(ITERATIONS_MOVE):
            sta.p.append(p1_drone[index])
        # iterate movement
        cont_move(master, sta, p1_drone[index], p2_drone[index])

    net.plotGraph(max_x=200, max_y=200)

    poll = Poll("Should we advance to P3 or return to P1?")

    info("* Starting network\n")
    net.build()
    net.socketServer(ip='127.0.0.1', port=12345, master=master, poll=poll)
    c1.start()
    ap1.start([c1])

    # open sockets in another thread
    for drone in master.get_all_drones():
        thread = threading.Thread(target=drone.open_socket, args=(drone.get_droneId(),))
        thread.daemon = True
        thread.start()
        thread = threading.Thread(target=drone.socket_loop)
        thread.daemon = True
        thread.start()

    _poll = poll
    
    result = poll.run_poll(master)
    max_polls = 2
    poll_count = 0
    while result  == "TIE" and poll_count < max_polls:
        poll_count += 1
        logger.info("Tie. Running poll again")
        result = poll.run_poll(master)

    if poll_count == max_polls:
        logger.info("Max polls reached")
        result = "ADVANCE"

    for drone in master.get_all_drones():
        server_send_message(master.sockets[drone.droneId], "Advance to P3" if result == "ADVANCE" else "Return to P1")

    if result == "ADVANCE":
        for index in range(len(master.get_all_drones())+1):
            if(index == len(master.get_all_drones())):
                sta = master.get_station()
            else:
                sta = master.get_all_drones()[index].get_station()
            # iterate movement
            cont_move(master, sta, p2_drone[index], p3_drone[index])

    # coming back to P1
    for index in range(len(master.get_all_drones())+1):
        if(index == len(master.get_all_drones())):
            sta = master.get_station()
        else:
            sta = master.get_all_drones()[index].get_station()
        # iterate movement
        cont_move(master, sta, p3_drone[index], p1_drone[index]) if result == "ADVANCE" else cont_move(master, sta, p2_drone[index], p1_drone[index])

    if True:
        verification_thread = threading.Thread(target=master.verify_every_x_seconds, args=(3,))
        verification_thread.daemon = True
        verification_thread.start()

    info("* Replaying Mobility\n")
    ReplayingMobility(net)

    info("* Running CLI\n")
    CLI(net)

    info("* Stopping network\n")
    net.stop()

if __name__ == '__main__':

    setLogLevel('info')
    topology()
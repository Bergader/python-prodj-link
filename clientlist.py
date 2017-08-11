import time
import logging

class ClientList:
  def __init__(self, client_change_callback=None):
    self.clients = []
    self.cb = client_change_callback

  def __len__():
    return len(self.clients)

  # adds client if it is not known yet, in any case it resets the ttl
  def eatKeepalive(self, keepalive_packet):
    c = next((x for x in self.clients if x.ip_addr == keepalive_packet["ip_addr"]), None)
    if c is None:
      c = Client()
      c.model = keepalive_packet["model"]
      c.ip_addr = keepalive_packet["ip_addr"]
      c.mac_addr = keepalive_packet["mac_addr"]
      c.player_number = keepalive_packet["player_number"]
      self.clients += [c]
      self.cb(self, c.player_number)
    else:
      n = keepalive_packet["player_number"]
      if c.player_number != n:
        logging.info("Player {} changed player number from {} to {}".format(c.ip_addr, c.player_number, n))
        c.player_number = n
        self.cb(self, c.player_number)
    c.updateTtl()

  def eatBeat(self, beat_packet):
    c = next((x for x in self.clients if x.player_number == beat_packet["player_number"]), None)
    if c is None: # packet from unknown client
      return
    c.updateTtl()
    if not c.status_packet_received:
      c.pitch = beat_packet["pitch"]
      c.bpm = beat_packet["bpm"]
      c.beat = beat_packet["beat"]
      self.cb(self, c.player_number)

  def eatStatus(self, status_packet):
    c = next((x for x in self.clients if x.player_number == status_packet["player_number"]), None)
    if c is None: # packet from unknown client
      return
    c.status_packet_received = True
    c.fw = status_packet["firmware"]
    c.bpm = status_packet["bpm"] if status_packet["bpm"] != 655.36 else "?"
    c.pitch = status_packet["physical_pitch"]
    c.actual_pitch = status_packet["actual_pitch"]
    c.beat = status_packet["beat"] if status_packet["beat"] != 0xffffffff else 0
    c.beat_count = status_packet["beat_count"] if status_packet["beat_count"] != 0xffffffff else "-"
    c.cue_distance = status_packet["cue_distance"] if status_packet["cue_distance"] != 511 else "-"
    c.play_state = status_packet["play_state"]
    c.usb_state = status_packet["usb_state"]
    c.sd_state = status_packet["sd_state"]
    c.player_slot = status_packet["loaded_slot"]
    c.state = [x for x in ["on_air","sync","master","play"] if status_packet["state"][x]==True]
    c.track_number = status_packet["track_number"]
    c.loaded_player_number = status_packet["loaded_player_number"]
    c.loaded_slot = status_packet["loaded_slot"]
    #c.on_air = status_packet["state"]["on_air"]
    #c.sync_on = status_packet["state"]["sync"]
    #c.master_on = status_packet["state"]["master"]
    #c.playing = status_packet["state"]["play"]
    c.updateTtl()
    logging.debug("eatStatus done")
    self.cb(self, c.player_number)

  # checks ttl and clears expired clients
  def gc(self):
    self.clients = [x for x in self.clients if not x.ttlExpired()]

  def getClientIps(self):
    return [client.ip_addr for client in self.clients]

class Client:
  def __init__(self):
    # device specific
    self.model = ""
    self.fw = ""
    self.ip_addr = ""
    self.mac_addr = ""
    self.player_number = 0
    # play state
    self.bpm = None
    self.pitch = 1
    self.actual_pitch = 1
    self.beat = None
    self.beat_count = None
    self.cue_distance = None
    self.play_state = "no_track"
    self.usb_state = "not_loaded"
    self.sd_state = "not_loaded"
    self.player_slot = "empty"
    #self.on_air = False
    #self.sync_on = False
    #self.master_on = False
    #self.playing = False
    self.state = []
    self.track_number = None
    self.loaded_player_number = 0
    self.loaded_slot = "empty"
    # internal use
    self.status_packet_received = False # ignore play state from beat packets
    self.ttl = time.time()

  def updateTtl(self):
    self.ttl = time.time()

  # drop clients after 5 seconds without keepalive packet
  def ttlExpired(self):
    return time.time()-self.ttl > 5

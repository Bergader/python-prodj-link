import socket
import packets
import logging
import time
from threading import Thread
from queue import Empty, Queue
from construct import FieldError, RangeError, MappingError, byte2int

metadata_type = {
  0x0001: "folder",
  0x0002: "album",
  0x0003: "disc",
  0x0004: "title",
  0x0006: "genre",
  0x0007: "artist",
  0x0008: "playlist",
  0x000a: "rating",
  0x000b: "duration",
  0x000d: "bpm",
  0x000e: "label",
  0x000f: "key",
  0x0010: "bitrate",
  0x0011: "year",
  0x0013: "color_none",
  0x0014: "color_pink",
  0x0015: "color_red",
  0x0016: "color_orange",
  0x0017: "color_yellow",
  0x0018: "color_green",
  0x0019: "color_aqua",
  0x001a: "color_blue",
  0x001b: "color_purple",
  0x0023: "comment",
  0x0028: "original_artist",
  0x0029: "remixer",
  0x002e: "date_added",
  0x0080: "root_genre",
  0x0081: "root_artist",
  0x0082: "root_album",
  0x0083: "root_track",
  0x0084: "root_playlist",
  0x0085: "root_bpm",
  0x0086: "root_rating",
  0x0087: "root_time",
  0x0088: "root_remixer",
  0x0089: "root_label",
  0x008a: "root_original_artist",
  0x008b: "root_key",
  0x008e: "root_color",
  0x0090: "root_folder",
  0x0091: "root_search",
  0x0092: "root_time",
  0x0093: "root_bitrate",
  0x0094: "root_filename",
  0x0095: "root_history",
  0x0098: "root_hot_cue_bank",
  0x0204: "title_and_album",
  0x0604: "title_and_genre",
  0x0704: "title_and_artist",
  0x0a04: "title_and_rating",
  0x0b04: "title_and_duration",
  0x0d04: "title_and_bpm",
  0x0e04: "title_and_label",
  0x0f04: "title_and_key",
  0x1004: "title_and_bitrate",
  0x1a04: "title_and_color",
  0x2304: "title_and_comment",
  0x2804: "title_and_original_artist",
  0x2904: "title_and_remixer",
  0x2a04: "title_and_dj_play_count",
  0x2e04: "title_and_date_added"
}

# columns depend on sort mode
sort_types = {
  "default": 0x00, # title | <depending on rekordbox configuration>
  "title": 0x01, # title | artist
  "artist": 0x02, # title | artist
  "album": 0x03, # title | album (+id)
  "bpm": 0x04, # title | bpm
  "rating": 0x05, # title | rating
  "genre": 0x06, # title | genre (+id)
  "comment": 0x07, # title | comment (+id)
  "duration": 0x08, # title | duration
  "remixer": 0x09, # title | remixer (+id)
  "label": 0x0a, # title | label (+id)
  "original_artist": 0x0b, # title | original artist (+id)
  "key": 0x0c, # title | key (+id)
  "bitrate": 0x0d, # title | bitrate
  "dj_play_count": 0x10, # title | dj_play_count
  "label": 0x11, # title | label (+id)
}

class DBClient(Thread):
  def __init__(self, prodj):
    super().__init__()
    self.cl = prodj.cl
    self.own_player_number = 0 # db queries seem to work if we submit player number 0 everywhere
    self.remote_ports = {} # dict {player_number: (ip, port)}
    self.socks = {} # dict of player_number: (sock, ttl, transaction_id)
    self.queue = Queue()

    self.metadata_store = {} # map of player_number,slot,track_id: metadata
    self.artwork_store = {} # map of player_number,slot,artwork_id: artwork_data
    self.waveform_store = {} # map of player_number,slot,artwork_id: waveform_data
    self.preview_waveform_store = {} # map of player_number,slot,artwork_id: preview_waveform_data
    self.beatgrid_store = {} # map of player_number,slot,artwork_id: beatgrid_data

  def start(self):
    self.keep_running = True
    super().start()

  def stop(self):
    self.keep_running = False
    self.join()

  def parse_metadata_payload(self, payload):
    entry = {}

    entry_id1 = payload[0]["value"]
    entry_id2 = payload[1]["value"]
    entry_string1 = payload[3]["value"]
    entry_string2 = payload[5]["value"]
    entry_type = payload[6]["value"]
    entry_id3 = payload[8]["value"]
    if entry_type not in metadata_type:
      logging.warning("DBClient: metadata type %d unknown", entry_type)
      logging.debug("DBClient: packet contents: %s", str(payload))
      return None
    entry_label = metadata_type[entry_type]

    if entry_label in ["duration", "rating", "disc", "dj_play_count", "bitrate"]:
      entry[entry_label] = entry_id2 # plain numbers
    elif entry_label == "bpm":
      entry[entry_label] = entry_id2/100
    elif entry_label == "title":
      entry[entry_label] = entry_string1
      entry["artwork_id"] = entry_id3
      entry["track_id"] = entry_id2
      entry["artist_id"] = entry_id1
    elif entry_label[:5] == "color":
      entry["color"] = entry_label[6:]
      entry["color_text"] = entry_string1
    elif entry_label in ["artist", "album", "comment", "genre", "original_artist", "remixer", "key", "label"]:
      entry[entry_label] = entry_string1
      entry[entry_label+"_id"] = entry_id1
    elif entry_label in ["date_added"]:
      entry[entry_label] = entry_string1
    elif entry_label == "playlist":
      entry["name"] = entry_string1
      entry["id"] = entry_id2
      entry["parent_id"] = entry_id1
    elif entry_label[:5] == "root_":
      entry["name"] = entry_string1
      entry["menu_id"] = entry_id2
    elif entry_label[:10] == "title_and_":
      entry_label1 = entry_label[:5] # "title"
      entry_label2 = entry_label[10:]
      entry[entry_label1] = entry_string1
      entry["artwork_id"] = entry_id3
      entry["track_id"] = entry_id2
      entry["artist_id"] = entry_id1
      entry_type2 = next((k for k,v in metadata_type.items() if v==entry_label2), None)
      if entry_type2 is None:
        logging.warning("DBClient: second column %s of %s not parseable", entry_type2, entry_type)
      else:
        entry2 = self.parse_metadata_payload([
          {"value": entry_id1}, {"value": entry_id1}, None, # duplicate entry1, as entry2 unused and swapped
          {"value": entry_string2}, None,
          {"value": ""}, {"value": entry_type2}, None,
          {"value": entry_id3}])
        if entry2 is not None:
          entry = {**entry, **entry2}
    else:
      logging.warning("DBClient: unhandled metadata type %s", entry_label)
      return None

    logging.debug("DBClient: parse_metadata %s", str(entry))
    return entry

  def parse_list(self, data):
    entries = [] # for list data
    for packet in data:
      # check packet types
      if packet["type"] == "menu_header":
        logging.debug("DBClient: parse_list menu_header")
        continue
      if packet["type"] == "menu_footer":
        logging.debug("DBClient: parse_list menu_footer")
        break
      if packet["type"] != "menu_item":
        logging.warning("DBClient: parse_list item not menu_item: {}".format(packet))
        continue

      # extract metadata from packet
      entry = self.parse_metadata_payload(packet["args"])
      if entry is None:
        continue
      entries += [entry]

    if data[-1]["type"] != "menu_footer":
      logging.warning("DBClient: list entries not ending with menu_footer")
    return entries

  def parse_metadata(self, data):
    md = {}
    for packet in data:
      # check packet types
      if packet["type"] == "menu_header":
        logging.debug("DBClient: parse_metadata menu_header")
        continue
      if packet["type"] == "menu_footer":
        logging.debug("DBClient: parse_metadata menu_footer")
        break
      if packet["type"] != "menu_item":
        logging.warning("DBClient: parse_metadata item not menu_item: {}".format(packet))
        continue

      # extract metadata from packet
      entry = self.parse_metadata_payload(packet["args"])
      if entry is None:
        continue
      md = {**md, **entry}

    if data[-1]["type"] != "menu_footer":
      logging.warning("DBClient: metadata packet not ending with menu_footer, buffer too small?")
    return md

  def receive_dbmessage(self, sock):
    recv_tries = 0
    data = b""
    while recv_tries < 30:
      data += sock.recv(4096)
      try:
        reply = packets.DBMessage.parse(data)
        return reply
      except RangeError as e:
        logging.debug("DBClient: Received %d bytes but parsing failed, trying to receive more", len(data))
        recv_tries += 1
    return None

  def query_list(self, player_number, slot, id_list, sort_mode, request_type):
    sock = self.getSocket(player_number)
    slot_id = byte2int(packets.PlayerSlot.build(slot)) if slot is not None else 0
    if sort_mode is None:
      sort_id = 0 # 0 for root_menu, playlist folders
    else:
      if sort_mode not in sort_types:
        logging.warning("DBClient: unknown sort mode %s", sort_mode)
        return None
      sort_id = sort_types[sort_mode]
    query = {
      "transaction_id": self.getTransactionId(player_number),
      "type": request_type,
      "args": [
        {"type": "int32", "value": self.own_player_number<<24 | 1<<16 | slot_id<<8 | 1}
      ]
    }
    # request-specific argument agumentations
    if request_type == "root_menu_request":
      query["args"].append({"type": "int32", "value": 0})
      query["args"].append({"type": "int32", "value": 0xffffff})
    elif request_type == "metadata_request":
      query["args"].append({"type": "int32", "value": id_list[0]})
    elif request_type == "playlist_request":
      query["args"].append({"type": "int32", "value": sort_id})
      query["args"].append({"type": "int32", "value": id_list[1] if id_list[1]>0 else id_list[0]})
      query["args"].append({"type": "int32", "value": 0 if id_list[1]>0 else 1}) # 1 -> get folder, 0 -> get playlist
    else: # for any (non-playlist) "*_by_*_request"
      query["args"].append({"type": "int32", "value": sort_id})
      for item_id in id_list:
        query["args"].append({"type": "int32", "value": item_id})
    data = packets.DBMessage.build(query)
    logging.debug("DBClient: query_list request: {}".format(query))
    sock.send(data)

    try:
      reply = self.receive_dbmessage(sock)
    except (RangeError, FieldError, MappingError, KeyError) as e:
      logging.error("DBClient: parsing %s query failed on player %d failed: %s", query["type"], player_number, str(e))
      return None
    if reply["type"] != "success":
      logging.error("DBClient: %s failed on player %d (got %s)", query["type"], player_number, reply["type"])
      return None
    entry_count = reply["args"][1]["value"]
    if entry_count == 0:
      logging.warning("DBClient: %s empty (0 entries)", request_type)
      return []
    logging.debug("DBClient: query_list %s: %d entries available", request_type, entry_count)

    # i could successfully receive hundreds of entries at once on xdj 1000
    # thus i do not fragment render requests here
    query = {
      "transaction_id": self.getTransactionId(player_number),
      "type": "render",
      "args": [
        {"type": "int32", "value": self.own_player_number<<24 | 1<<16 | slot_id<<8 | 1},
        {"type": "int32", "value": 0}, # entry offset
        {"type": "int32", "value": entry_count}, # entry count
        {"type": "int32", "value": 0},
        {"type": "int32", "value": entry_count}, # entry count
        {"type": "int32", "value": 0}
      ]
    }
    data = packets.DBMessage.build(query)
    logging.debug("DBClient: render query {}".format(query))
    sock.send(data)
    recv_tries = 0
    data = b""
    while recv_tries < 40:
      data += sock.recv(4096)
      try:
        reply = packets.ManyDBMessages.parse(data)
      except (RangeError, FieldError):
        logging.debug("DBClient: failed to parse %s render reply (%d bytes), trying to receive more", request_type, len(data))
        recv_tries += 1
      else:
        if reply[-1]["type"] != "menu_footer":
          logging.debug("DBClient: %s rendering without menu_footer @ %d bytes, trying to receive more", request_type, len(data))
          recv_tries += 1
        else:
          break
    if recv_tries >= 40:
      logging.error("DBClient: Failed to receive %s render reply after %d tries", request_type, recv_tries)
      return None

    if request_type == "metadata_request":
      parsed = self.parse_metadata(reply)
    else:
      parsed = self.parse_list(reply)
    return parsed

  def query_blob(self, player_number, slot, item_id, request_type, location=8):
    sock = self.getSocket(player_number)
    slot_id = byte2int(packets.PlayerSlot.build(slot))
    query = {
      "transaction_id": self.getTransactionId(player_number),
      "type": request_type,
      "args": [
        {"type": "int32", "value": self.own_player_number<<24 | location<<16 | slot_id<<8 | 1},
        {"type": "int32", "value": item_id}
      ]
    }
    # request-specific argument agumentations
    if request_type == "waveform_request":
      query["args"].append({"type": "int32", "value": 0})
    elif request_type == "preview_waveform_request":
      query["args"].insert(1, {"type": "int32", "value": 4})
      query["args"].append({"type": "int32", "value": 0})
    logging.debug("DBClient: {} query {}".format(request_type, query))
    data = packets.DBMessage.build(query)
    sock.send(data)
    try:
      reply = self.receive_dbmessage(sock)
    except (RangeError, FieldError, MappingError, KeyError) as e:
      logging.error("DBClient: %s query parse error: %s", request_type, str(e))
      return None
    if reply is None:
      logging.error("Failed to receive %s blob (%d tries)", request_type, recv_tries)
      return None
    if reply["type"] == "invalid_request" or reply["args"][2]["value"] == 0:
      logging.error("DBClient: %s blob query failed on player %d (got %s)", query["type"], player_number, reply["type"])
      return None
    blob = reply["args"][3]["value"]
    logging.debug("DBClient: got %d bytes of blob data", len(blob))
    return blob

  def get_server_port(self, player_number):
    if player_number not in self.remote_ports:
      client = self.cl.getClient(player_number)
      if client is None:
        logging.error("DBClient: client {} not found".format(player_number))
        return
      sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      sock.connect((client.ip_addr, packets.DBServerQueryPort))
      sock.send(packets.DBServerQuery.build({}))
      data = sock.recv(2)
      sock.close()
      port = packets.DBServerReply.parse(data)
      self.remote_ports[player_number] = (client.ip_addr, port)
      logging.info("DBClient port of player {}: {}".format(player_number, port))
    return self.remote_ports[player_number]

  def send_initial_packet(self, sock):
    init_packet = packets.DBFieldFixed("int32")
    sock.send(init_packet.build(1))
    data = sock.recv(16)
    try:
      reply = init_packet.parse(data)
      logging.debug("DBClient: initial packet reply %d", reply)
    except:
      logging.warning("DBClient: failed to parse initial packet reply, ignoring")

  def send_setup_packet(self, sock, player_number):
    query = {
      "transaction_id": 0xfffffffe,
      "type": "setup",
      "args": [{"type": "int32", "value": self.own_player_number}]
    }
    sock.send(packets.DBMessage.build(query))
    data = sock.recv(48)
    if len(data) == 0:
      logging.error("Failed to connect to player {}".format(player_number))
      return
    reply = packets.DBMessage.parse(data)
    logging.info("DBClient: connected to player {}".format(reply["args"][1]["value"]))

  def getTransactionId(self, player_number):
    sock_info = self.socks[player_number]
    self.socks[player_number] = (sock_info[0], sock_info[1], sock_info[2]+1)
    return sock_info[2]

  def resetSocketTtl(self, player_number):
    sock = self.socks[player_number]
    self.socks[player_number] = (sock[0], 30, sock[2])

  def gc(self):
    for player_number in list(self.socks):
      sock = self.socks[player_number]
      if sock[1] <= 0:
        logging.info("Closing DB socket of player %d", player_number)
        self.closeSocket(player_number)
      else:
        self.socks[player_number] = (sock[0], sock[1]-1, sock[2])

  def getSocket(self, player_number):
    if player_number in self.socks:
      self.resetSocketTtl(player_number)
      return self.socks[player_number][0]

    ip_port = self.get_server_port(player_number)
    if ip_port is None:
      logging.error("DBClient: failed to get remote port of player {}".format(player_number))
      return

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
    sock.connect(ip_port)
    self.socks[player_number] = (sock, 30, 1) # socket, ttl, transaction_id

    # send connection initialization packet
    self.send_initial_packet(sock)
    # first query
    self.send_setup_packet(sock, player_number)

    return sock

  def closeSocket(self, player_number):
    if player_number in self.socks:
      self.socks[player_number][0].close()
      del self.socks[player_number]
    else:
      logging.warning("Requested to delete unexistant socket for player %d", player_number)

  # called from outside, enqueues request
  def get_metadata(self, player_number, slot, track_id, callback=None):
    self._enqueue_request("metadata", self.metadata_store, (player_number, slot, track_id), callback)

  def get_root_menu(self, player_number, slot, callback=None):
    self._enqueue_request("root_menu", None, (player_number, slot), callback)

  def get_titles(self, player_number, slot, sort_mode="default", callback=None):
    self._enqueue_request("title", None, (player_number, slot, [], sort_mode), callback)

  def get_titles_by_album(self, player_number, slot, album_id, sort_mode="default", callback=None):
    self._enqueue_request("title_by_album", None, (player_number, slot, [album_id], sort_mode), callback)

  def get_artists(self, player_number, slot, sort_mode="default", callback=None):
    self._enqueue_request("artist", None, (player_number, slot, [], sort_mode), callback)

  def get_albums_by_artist(self, player_number, slot, artist_id, sort_mode="default", callback=None):
    self._enqueue_request("album_by_artist", None, (player_number, slot, [artist_id], sort_mode), callback)

  def get_titles_by_artist_album(self, player_number, slot, artist_id, album_id, sort_mode="default", callback=None):
    self._enqueue_request("title_by_artist_album", None, (player_number, slot, [artist_id, album_id], sort_mode), callback)

  def get_playlists(self, player_number, slot, folder_id=0, callback=None):
    self._enqueue_request("playlist", None, (player_number, slot, [folder_id, 0], None), callback)

  def get_playlist(self, player_number, slot, folder_id, playlist_id, sort_mode="default", callback=None):
    self._enqueue_request("playlist", None, (player_number, slot, [folder_id, playlist_id], sort_mode), callback)

  def get_artwork(self, player_number, slot, artwork_id, callback=None):
    self._enqueue_request("artwork", self.artwork_store, (player_number, slot, artwork_id), callback)

  def get_waveform(self, player_number, slot, track_id, callback=None):
    self._enqueue_request("waveform", self.waveform_store, (player_number, slot, track_id), callback)

  def get_preview_waveform(self, player_number, slot, track_id, callback=None):
    self._enqueue_request("preview_waveform", self.preview_waveform_store, (player_number, slot, track_id), callback)

  def get_beatgrid(self, player_number, slot, track_id, callback=None):
    self._enqueue_request("beatgrid", self.beatgrid_store, (player_number, slot, track_id), callback)

  def _enqueue_request(self, request, store, params, callback):
    player_number = params[0]
    if player_number == 0 or player_number > 4:
      logging.warning("DBClient: invalid %s request parameters", request)
      return
    logging.debug("DBClient: enqueueing %s request with params %s", request, str(params))
    self.queue.put((request, store, params, callback))

  def _handle_request(self, request, store, params, callback):
    if store is not None and len(params) == 3 and params in store:
      logging.debug("DBClient: %s request params %s already known", request, str(params))
      if request == "metadata":
        self.cl.storeMetadataByLoadedTrack(*params, store[params])
      if callback is not None:
        callback(request, *params, store[params])
      return
    logging.debug("DBClient: handling %s request params %s", request, str(params))
    if request == "metadata":
      reply = self.query_list(*params[:2], [params[2]], None, "metadata_request")
      self.cl.storeMetadataByLoadedTrack(*params, reply)
    elif request == "root_menu":
      reply = self.query_list(*params, None, None, "root_menu_request")
    elif request == "title":
      reply = self.query_list(*params, "title_request")
    elif request == "title_by_album":
      reply = self.query_list(*params, "title_by_album_request")
    elif request == "artist":
      reply = self.query_list(*params, "artist_request")
    elif request == "album_by_artist":
      reply = self.query_list(*params, "album_by_artist_request")
    elif request == "title_by_artist_album":
      reply = self.query_list(*params, "title_by_artist_album_request")
    elif request == "playlist":
      reply = self.query_list(*params, "playlist_request")
    elif request == "artwork":
      reply = self.query_blob(*params, "artwork_request")
    elif request == "waveform":
      reply = self.query_blob(*params, "waveform_request", 1)
    elif request == "preview_waveform":
      reply = self.query_blob(*params, "preview_waveform_request")
    elif request == "beatgrid":
      reply = self.query_blob(*params, "beatgrid_request")
      try: # pre-parse beatgrid data (like metadata) for easier access
        reply = packets.Beatgrid.parse(reply)
      except (RangeError, FieldError) as e:
        logging.error("DBClient: failed to parse beatgrid data: %s", e)
        reply = None
    else:
      logging.error("DBClient: invalid request type %s", request)
      return
    if store is not None:
      store[params] = reply
    if callback is not None:
      callback(request, *params, reply)

  def run(self):
    logging.debug("DBClient starting")
    while self.keep_running:
      try:
        request = self.queue.get(timeout=1)
      except Empty:
        self.gc()
        continue
      client = self.cl.getClient(request[2][0])
      if not client:
        logging.warning("DBClient: player %s not found in clientlist, discarding %s request", request[2], request[0])
        self.queue.task_done()
        continue
      if (request[0] in ["metadata_request", "artwork_request", "preview_waveform_request", "beatgrid_request", "waveform_request"]
          and client.play_state in ["no_track", "loading_track", "cannot_play_track", "emergency"]):
        logging.debug("DBClient: delaying %s request due to play state: %s", request[0], client.play_state)
        self.queue.task_done()
        self.queue.put(request)
        time.sleep(1)
        continue
      self._handle_request(*request)
      self.queue.task_done()
    logging.debug("DBClient shutting down")

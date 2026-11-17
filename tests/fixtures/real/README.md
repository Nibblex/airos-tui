# Configuración de un equipo real

`airos8.7.14-nanostation5ac-sta.*`: la configuración (`/getcfg.cgi`) y el `board.info` de una
NanoStation 5AC loco con airOS 8.7.14 en modo estación PtMP, guardada por su propia web, leída
el 7-11-2026. El usuario, el nombre del equipo, el SSID, las claves WPA y la MAC de la placa
están cambiados por valores ficticios; lo demás es tal cual.

`test_wireless.py` comprueba con ella que el modelo de la web de airOS 8 de airos-tui, al
guardar sin cambios, no modifica ni añade ninguna clave.

`airos8.7.25-litebeam5ac-ap-ptp.status.json`: el `status.cgi` de una LiteBeam 5AC con airOS
8.7.25 en modo punto de acceso PtP, con su estación (otra LiteBeam 5AC con 8.7.21) conectada,
leído el 8-11-2026. Los nombres, el SSID, las MAC, las IP y los identificadores están cambiados
por valores ficticios; lo demás es tal cual. `test_stations.py` comprueba con él lo que muestra
la página principal de la web de los dos extremos (Ethernet, CINR, tiempo en servicio…).

`airos8.7.25-speedtest-status.json`: la última respuesta de `GET /sptest_action.cgi?action=status`
de esa misma LiteBeam 5AC (8.7.25) en una prueba de velocidad de 10 s en dúplex contra su
estación, del 8-11-2026: el estado 9 al terminar, la duración y unas tres muestras por segundo
de cada sentido (Mbps). No lleva nada que identifique el enlace. `test_tools.py` comprueba con
ella cómo lee la TUI el final de una prueba.

`airos8.7.25-firewall-user-rules.txt`, `airos8.7.25-port-forward-off-es.txt` y
`airos8.7.25-dhcp-leases-off.json`: lo que dio esa misma LiteBeam 5AC (8.7.25, modo puente con
la configuración de red avanzada y su web en español) el 9-11-2026 a `GET
/api/firewall/user-rules` (el listado de ebtables de su única regla de firewall, la que bloquea
el DHCP que llega por la WLAN), a `GET /api/firewall/port-forward` (su aviso de que la
redirección de puertos está apagada, en su idioma) y a `GET /api/dhcp-server/leases` (sin
servidor DHCP). No llevan nada que identifique el equipo. `test_router.py` comprueba con ellos
que el simulador da el mismo listado para esa regla y cómo lee la TUI las concesiones.

`airos8.7.14-nanostation5ac-discovery.bin`: la respuesta de una NanoStation 5AC loco con airOS
8.7.14 a la consulta v1 del descubrimiento de Ubiquiti, del 9-11-2026. La MAC, el nombre y su
IP automática (que sale de la MAC) están cambiados; lo demás es tal cual. `test_discovery.py`
comprueba con ella cómo la lee la TUI.

`airos8.7.14-router-user-rules-1.txt`, `airos8.7.14-router-user-rules-2.txt`,
`airos8.7.14-router-port-forward-1.txt`, `airos8.7.14-router-port-forward-2.txt` y
`airos8.7.14-bridge-user-rules.txt`: lo que listó esa misma NanoStation (8.7.14), en modo prueba,
en `GET /api/firewall/user-rules` y `GET /api/firewall/port-forward` con las reglas que pone
`test_router.py` (modo router; y modo puente con la configuración de red avanzada), el
9-11-2026. Las IP son las de la red de prueba; no llevan nada que identifique el equipo.
`test_router.py` comprueba que el simulador lista lo mismo (con los contadores a 0).

`airos8.7.14-nanostation5ac-airviewdata.json.gz`: una respuesta de `GET /airviewdata.cgi` (su
airView) de esa misma NanoStation (8.7.14), tal cual, comprimida, del 9-11-2026: el espectro que
oía (de 5095,625 a 5904,375 MHz), su histograma, sus potencias y su ruido ambiente (24 minutos y
11 horas). No lleva nada que identifique el equipo. `test_airview.py` comprueba con ella el
modelo de la web (sus niveles, su cascada, sus etiquetas) y cómo la dibuja la TUI.

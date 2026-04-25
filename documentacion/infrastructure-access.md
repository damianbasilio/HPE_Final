## Acceso a las máquinas

Se puede acceder a las máquinas mediante **SSH** o **RDP**, según la necesidad.

### Acceso por SSH (recomendado)

Es la forma más rápida y fluida de trabajar.

#### Requisitos

* Cliente SSH (Linux/macOS o terminal en Windows)

#### Conexión

```bash
ssh usuario@IP_MAQUINA
```

#### Ventajas

* Menor latencia
* Mayor estabilidad
* Consumo mínimo de recursos
* Ideal para administración y ejecución de comandos

### Acceso por RDP

Permite acceder a la máquina con entorno gráfico.

#### Requisitos

* Cliente de escritorio remoto (por ejemplo, Conexión a Escritorio Remoto en Windows)

#### Conexión

1. Abrir el cliente de RDP
2. Introducir la IP de la máquina y puerto 3389 Ej.: 10.10.48.35:3389
3. Introducir usuario y contraseña

#### Consideraciones

* Mayor consumo de recursos
* Puede ser menos fluido que SSH
* Recomendado solo si se necesita entorno gráfico
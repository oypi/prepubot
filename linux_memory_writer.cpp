#include <pybind11/pybind11.h>
#include <sys/uio.h>
#include <unistd.h>
#include <thread>
#include <atomic>
#include <string>
#include <mutex>
#include <cstring>
#include <iostream>

namespace py = pybind11;

class MemoryWriter {
public:
    MemoryWriter() : pid(0), running(false), address(0) {}

    bool open_process(const std::string& processName) {
        return true;
    }

    bool open_process_by_id(int processID) {
        pid = processID;
        return pid > 0;
    }

    void start() {
        if (pid > 0 && !running) {
            running = true;
            worker = std::thread(&MemoryWriter::write_memory, this);
        }
    }

    void stop() {
        running = false;
        if (worker.joinable()) {
            worker.join();
        }
    }

    void set_memory_data(uintptr_t new_address, const std::string& new_data) {
        std::lock_guard<std::mutex> lock(data_mutex);
        address = new_address;
        data = new_data;
    }

    ~MemoryWriter() {
        stop();
    }

private:
    int pid;
    std::atomic<bool> running;
    std::thread worker;
    uintptr_t address;
    std::string data;
    std::mutex data_mutex;

    void write_memory() {
        struct iovec local_iov;
        struct iovec remote_iov;
        char local_data[64];
        while (running) {
            uintptr_t local_address = 0;
            size_t data_len = 0;
            {
                std::lock_guard<std::mutex> lock(data_mutex);
                local_address = address;
                data_len = data.size();
                if (data_len > 64) data_len = 64;
                if (data_len > 0) {
                    std::memcpy(local_data, data.data(), data_len);
                }
            }

            if (local_address && data_len > 0) {
                local_iov.iov_base = local_data;
                local_iov.iov_len = data_len;
                remote_iov.iov_base = reinterpret_cast<void*>(local_address);
                remote_iov.iov_len = data_len;
                process_vm_writev(pid, &local_iov, 1, &remote_iov, 1, 0);
            }
            usleep(250); // ~4000 Hz microsecond precision C++ thread
        }
    }
};

PYBIND11_MODULE(memory_writer, m) {
    py::class_<MemoryWriter>(m, "MemoryWriter")
        .def(py::init<>())
        .def("open_process", &MemoryWriter::open_process)
        .def("open_process_by_id", &MemoryWriter::open_process_by_id)
        .def("start", &MemoryWriter::start)
        .def("stop", &MemoryWriter::stop)
        .def("set_memory_data", &MemoryWriter::set_memory_data);
}
